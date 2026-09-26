from __future__ import annotations

import json
import subprocess

import jsonschema
import pytest
import yaml
from contracts_lib import (
    CONTRACTS,
    contracts_base_ref,
    iter_schemas,
    load,
    registry,
    schema_property_names,
    schema_property_paths,
    validator_for,
)
from referencing import Registry, Resource

PII_NAMES = {"email", "phone", "name", "full_name", "address", "dob", "birth_date", "nik", "ktp"}
# Any property about a child (child_name, child_age, ...) is `child` class data,
# which classification.yaml never allows in events.
PII_PREFIXES = ("child_",)


def pii_hits(names):
    return {n for n in names if n in PII_NAMES or n.startswith(PII_PREFIXES)}


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
    assert not pii_hits(names), pii_hits(names)


def test_pii_denylist_catches_child_prefix():
    assert pii_hits({"child_age", "child_school", "work_order_id", "children_count"}) == {"child_age", "child_school"}


def test_kido_profile_validates():
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(_yaml("agents/kido.yaml"))


def test_friday_profile_validates():
    validator_for(CONTRACTS / "agents/dev_agent_profile.v1.schema.json").validate(_yaml("agents/friday.yaml"))


def test_kido_chat_profile_validates():
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(_yaml("agents/kido_chat.yaml"))


def test_kido_profile_still_validates_unchanged():
    # kido.yaml predates data_scope.llm; absent llm means "no personal data to any LLM"
    # (README / classification.yaml notes) -- pin that it is genuinely absent, not just valid.
    kido = _yaml("agents/kido.yaml")
    assert "llm" not in kido["data_scope"]
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(kido)


@pytest.mark.parametrize("bad_tool", ["confirm_order", "refund"])
def test_chat_tools_rejects_tools_outside_the_allowed_enum(bad_tool):
    profile = _yaml("agents/kido_chat.yaml")
    profile["chat_tools"] = [*profile["chat_tools"], bad_tool]
    with pytest.raises(jsonschema.ValidationError):
        validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


@pytest.mark.parametrize("n", [1, 4, 8])
def test_max_tool_calls_per_turn_allows_1_to_8(n):
    profile = _yaml("agents/kido_chat.yaml")
    profile["max_tool_calls_per_turn"] = n
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


@pytest.mark.parametrize("n", [0, 9, -1])
def test_max_tool_calls_per_turn_rejects_outside_1_to_8(n):
    profile = _yaml("agents/kido_chat.yaml")
    profile["max_tool_calls_per_turn"] = n
    with pytest.raises(jsonschema.ValidationError):
        validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


def test_data_scope_forbidden_rejects_duplicate_entries():
    profile = _yaml("agents/kido_chat.yaml")
    profile["data_scope"]["forbidden"] = ["financial", "financial"]
    with pytest.raises(jsonschema.ValidationError):
        validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


def test_personal_max_classification_rejects_third_party_llm():
    profile = _yaml("agents/kido_chat.yaml")
    profile["data_scope"]["llm"] = "third_party"
    with pytest.raises(jsonschema.ValidationError):
        validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


def test_personal_max_classification_still_allows_vetted_llm():
    # The if/then only blocks third_party; vetted stays valid for personal (kido_chat's own case).
    profile = _yaml("agents/kido_chat.yaml")
    assert profile["data_scope"]["max_classification"] == "personal"
    assert profile["data_scope"]["llm"] == "vetted"
    validator_for(CONTRACTS / "agents/profile.v1.schema.json").validate(profile)


def _llm_route_allowed(levels, max_classification, llm):
    """True iff classification.yaml's flag for this (max_classification, llm) pair is set."""
    return levels[max_classification][f"allowed_to_{llm}_llm"]


def test_llm_route_allowed_helper_flags_a_mismatch():
    levels = {"personal": {"allowed_to_vetted_llm": True, "allowed_to_third_party_llm": False}}
    assert _llm_route_allowed(levels, "personal", "vetted") is True
    assert _llm_route_allowed(levels, "personal", "third_party") is False


def test_agent_profiles_llm_route_matches_classification_flags():
    # Cross-file: any contracts/agents/*.yaml declaring data_scope.llm must name a route the
    # classification.yaml flag for its max_classification actually allows.
    levels = {c["name"]: c for c in _yaml("classification.yaml")["levels"]}
    checked = 0
    for path in sorted((CONTRACTS / "agents").glob("*.yaml")):
        profile = _yaml(f"agents/{path.name}")
        data_scope = profile.get("data_scope") or {}
        llm = data_scope.get("llm")
        if llm is None:
            continue
        checked += 1
        assert _llm_route_allowed(levels, data_scope["max_classification"], llm), (
            f"{path.name}: max_classification={data_scope['max_classification']!r} llm={llm!r} "
            "not allowed by classification.yaml"
        )
    assert checked >= 1, "expected at least one agents/*.yaml with data_scope.llm (kido_chat.yaml)"


def test_registry_lists_kido_chat_as_an_order_requested_producer():
    entry = next(e for e in _yaml("events/registry.yaml")["events"] if e["type"] == "order.requested")
    assert isinstance(entry["producer"], list)
    assert any("kido_chat" in p for p in entry["producer"])
    assert any("order_intake" in p for p in entry["producer"])


def test_data_scope_max_classification_enum_is_exactly_public_internal_personal():
    schema = load(CONTRACTS / "agents/profile.v1.schema.json")
    enum = schema["properties"]["data_scope"]["properties"]["max_classification"]["enum"]
    assert set(enum) == {"public", "internal", "personal"}


def test_data_scope_forbidden_enum_is_exactly_financial_child():
    schema = load(CONTRACTS / "agents/profile.v1.schema.json")
    enum = schema["properties"]["data_scope"]["properties"]["forbidden"]["items"]["enum"]
    assert set(enum) == {"financial", "child"}


def test_data_scope_llm_enum_is_exactly_vetted_third_party():
    schema = load(CONTRACTS / "agents/profile.v1.schema.json")
    enum = schema["properties"]["data_scope"]["properties"]["llm"]["enum"]
    assert set(enum) == {"vetted", "third_party"}


def test_agent_profile_name_matches_its_file_stem():
    for path in sorted((CONTRACTS / "agents").glob("*.yaml")):
        profile = _yaml(f"agents/{path.name}")
        assert profile["profile"] == path.stem, f"{path.name}: profile field is {profile['profile']!r}"


def test_personal_class_allowed_to_vetted_llm_but_not_third_party():
    levels = {c["name"]: c for c in _yaml("classification.yaml")["levels"]}
    assert levels["personal"]["allowed_to_vetted_llm"] is True
    assert levels["personal"]["allowed_to_third_party_llm"] is False


@pytest.mark.parametrize("name", ["financial", "child"])
def test_financial_and_child_blocked_from_every_llm_route(name):
    levels = {c["name"]: c for c in _yaml("classification.yaml")["levels"]}
    assert levels[name]["allowed_to_vetted_llm"] is False
    assert levels[name]["allowed_to_third_party_llm"] is False


def test_every_level_except_personal_has_matching_llm_route_flags():
    for level in _yaml("classification.yaml")["levels"]:
        if level["name"] == "personal":
            continue
        assert level["allowed_to_vetted_llm"] == level["allowed_to_third_party_llm"], level["name"]


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
def test_schema_is_backward_compatible_with_base(path):
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = path.relative_to(CONTRACTS.parent).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=CONTRACTS.parent, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    old, new = json.loads(shown.stdout), load(path)
    # Resolved against the CURRENT registry: refs like envelope.v1's $id are
    # stable across versions, and each schema is also checked against the base in
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


# --- unit tests for contracts_base_ref (the compat check's comparison ref) ---


class _FakeGit:
    """Stands in for subprocess.run: maps a git argv tail to (returncode, stdout)."""

    def __init__(self, answers, missing=False):
        self.answers = answers
        self.missing = missing

    def __call__(self, argv, **_kwargs):
        if self.missing:
            raise FileNotFoundError("git")
        code, out = self.answers.get(tuple(argv[1:]), (128, ""))
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


_HEAD = ("rev-parse", "HEAD")
_MERGE_BASE = ("merge-base", "HEAD", "origin/main")


def test_base_ref_prefers_explicit_env():
    git = _FakeGit({_HEAD: (0, "aaa\n"), _MERGE_BASE: (0, "bbb\n")})
    assert contracts_base_ref(run=git, env={"CONTRACTS_BASE_REF": "v1.2.3"}) == "v1.2.3"


def test_base_ref_uses_merge_base_with_origin_main():
    git = _FakeGit({_HEAD: (0, "aaa\n"), _MERGE_BASE: (0, "bbb\n")})
    assert contracts_base_ref(run=git, env={"CONTRACTS_BASE_REF": ""}) == "bbb"


def test_base_ref_falls_back_to_parent_when_merge_base_is_head():
    # A push to main: HEAD *is* origin/main, so the merge-base compares HEAD with itself.
    git = _FakeGit({_HEAD: (0, "aaa\n"), _MERGE_BASE: (0, "aaa\n")})
    assert contracts_base_ref(run=git, env={}) == "HEAD~1"


def test_base_ref_falls_back_to_parent_without_origin_main():
    git = _FakeGit({_HEAD: (0, "aaa\n")})
    assert contracts_base_ref(run=git, env={}) == "HEAD~1"


def test_base_ref_is_none_without_git_locally():
    assert contracts_base_ref(run=_FakeGit({}, missing=True), env={}) is None
    assert contracts_base_ref(run=_FakeGit({}), env={}) is None  # not a git checkout


def test_base_ref_fails_without_git_in_ci():
    with pytest.raises(RuntimeError, match="git"):
        contracts_base_ref(run=_FakeGit({}, missing=True), env={"CI": "true"})
    with pytest.raises(RuntimeError, match="git"):
        contracts_base_ref(run=_FakeGit({}), env={"CI": "true"})
