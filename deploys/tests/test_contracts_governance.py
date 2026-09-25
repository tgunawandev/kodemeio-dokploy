from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from contracts_lib import (
    CONTRACTS,
    iter_schemas,
    load,
    registry,
    schema_property_names,
    schema_property_paths,
    validator_for,
)
from referencing import Registry, Resource

PII_NAMES = {"email", "phone", "name", "full_name", "address", "dob", "birth_date", "nik", "ktp"}


def _yaml(rel):
    return yaml.safe_load((CONTRACTS / rel).read_text())


def test_registry_covers_every_event_schema():
    registered = {entry["schema"] for entry in _yaml("events/registry.yaml")["events"]}
    on_disk = {p.relative_to(CONTRACTS).as_posix() for p in (CONTRACTS / "events").glob("*.schema.json")}
    on_disk.discard("events/envelope.v1.schema.json")
    assert registered == on_disk


def test_registry_entries_are_complete():
    for entry in _yaml("events/registry.yaml")["events"]:
        assert {"type", "schema", "producer", "consumers", "replay", "retention_days"} <= entry.keys()


def test_event_classes_are_allowed_in_events():
    allowed = {c["name"] for c in _yaml("classification.yaml")["levels"] if c["allowed_in_events"]}
    envelope = load(CONTRACTS / "events/envelope.v1.schema.json")
    assert set(envelope["properties"]["data_classification"]["enum"]) <= allowed
    assert "financial" not in allowed and "child" not in allowed


@pytest.mark.parametrize("path", sorted((CONTRACTS / "events").glob("*.schema.json")), ids=lambda p: p.name)
def test_no_pii_property_names_in_events(path):
    names = schema_property_names(load(path), registry())
    assert not (names & PII_NAMES), names & PII_NAMES


def test_kido_profile_validates():
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(_yaml("agents/kido.yaml"))


def test_policy_lists_always_human_classes():
    classes = set(_yaml("approvals/policy.v1.yaml")["always_human"])
    assert {
        "production_deploy",
        "schema_migration",
        "dns_security_permission",
        "destructive_delete",
        "money_movement",
    } <= classes


@pytest.mark.parametrize("path", iter_schemas(), ids=lambda p: p.name)
def test_schema_is_backward_compatible_with_head(path):
    rel = path.relative_to(CONTRACTS.parent).as_posix()
    shown = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=CONTRACTS.parent, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip("new schema, nothing to compare")
    old, new = json.loads(shown.stdout), load(path)
    # Resolved against the CURRENT registry: refs like envelope.v1's $id are
    # stable across versions, and each schema is also checked against HEAD in
    # its own right, so this doesn't hide a break in a ref'd schema.
    reg = registry()
    old_paths, old_required = schema_property_paths(old, reg)
    new_paths, new_required = schema_property_paths(new, reg)
    removed = old_paths - new_paths
    newly_required = new_required - old_required
    assert not removed, f"removed properties need a new major version: {removed}"
    assert not newly_required, f"newly required properties need a new major version: {newly_required}"


# --- unit tests for the schema_property_paths / schema_property_names helpers ---

_NESTED_BASE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "payload": {
            "type": "object",
            "required": ["work_order_id", "lines"],
            "properties": {
                "work_order_id": {"type": "string"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["product_ref", "quantity"],
                        "properties": {
                            "product_ref": {"type": "string"},
                            "quantity": {"type": "integer"},
                        },
                    },
                },
            },
        }
    },
}

_EMPTY_REGISTRY = Registry()


def test_schema_property_paths_flags_removed_nested_field():
    new_schema = json.loads(json.dumps(_NESTED_BASE))
    del new_schema["properties"]["payload"]["properties"]["lines"]["items"]["properties"]["quantity"]
    new_schema["properties"]["payload"]["properties"]["lines"]["items"]["required"] = ["product_ref"]

    old_paths, _ = schema_property_paths(_NESTED_BASE, _EMPTY_REGISTRY)
    new_paths, _ = schema_property_paths(new_schema, _EMPTY_REGISTRY)

    removed = old_paths - new_paths
    assert removed == {"payload.lines[].quantity"}


def test_schema_property_paths_flags_newly_required_nested_field():
    new_schema = json.loads(json.dumps(_NESTED_BASE))
    items = new_schema["properties"]["payload"]["properties"]["lines"]["items"]
    items["properties"]["sku"] = {"type": "string"}
    items["required"].append("sku")

    _, old_required = schema_property_paths(_NESTED_BASE, _EMPTY_REGISTRY)
    _, new_required = schema_property_paths(new_schema, _EMPTY_REGISTRY)

    newly_required = new_required - old_required
    assert newly_required == {"payload.lines[].sku"}


def test_schema_property_paths_allows_optional_nested_addition():
    new_schema = json.loads(json.dumps(_NESTED_BASE))
    new_schema["properties"]["payload"]["properties"]["lines"]["items"]["properties"]["note"] = {"type": "string"}
    # deliberately NOT added to "required"

    old_paths, old_required = schema_property_paths(_NESTED_BASE, _EMPTY_REGISTRY)
    new_paths, new_required = schema_property_paths(new_schema, _EMPTY_REGISTRY)

    assert old_paths - new_paths == set()
    assert new_required - old_required == set()
    assert "payload.lines[].note" in new_paths - old_paths


def test_schema_property_names_resolves_ref():
    target = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:test:contact-target",
        "type": "object",
        "properties": {"email": {"type": "string"}},
    }
    reg = Registry().with_resource("urn:test:contact-target", Resource.from_contents(target))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"contact": {"$ref": "urn:test:contact-target"}},
    }

    names = schema_property_names(schema, reg)

    assert "email" in names
