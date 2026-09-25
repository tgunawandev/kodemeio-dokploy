from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from contracts_lib import CONTRACTS, iter_schemas, load, validator_for

PII_NAMES = {"email", "phone", "name", "full_name", "address", "dob", "birth_date", "nik", "ktp"}


def _yaml(rel):
    return yaml.safe_load((CONTRACTS / rel).read_text())


def _property_names(node):
    if isinstance(node, dict):
        for key, value in node.get("properties", {}).items():
            yield key
            yield from _property_names(value)
        for key in ("items", "allOf", "anyOf", "oneOf"):
            value = node.get(key)
            for child in value if isinstance(value, list) else [value] if value else []:
                yield from _property_names(child)


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
    names = set(_property_names(load(path)))
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
    removed = set(old.get("properties", {})) - set(new.get("properties", {}))
    newly_required = set(new.get("required", [])) - set(old.get("required", []))
    assert not removed, f"removed properties need a new major version: {removed}"
    assert not newly_required, f"newly required properties need a new major version: {newly_required}"
