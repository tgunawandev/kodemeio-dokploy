from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/contractor_readiness.py"
SPEC = importlib.util.spec_from_file_location("contractor_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
G5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G5)


def event(action: str = "joiner") -> dict:
    change = {"old_scope_ref": None, "new_scope_ref": None, "approval_ref": None}
    revocation = {
        "authentik": {
            "status": None,
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": None,
        },
        "mattermost_sessions": {
            "status": None,
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": None,
        },
        "mattermost_memberships": {
            "status": None,
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": None,
        },
        "owned_tokens": {
            "status": None,
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": None,
        },
        "evidence_ref": None,
    }
    if action == "mover":
        change = {"old_scope_ref": "scope:old", "new_scope_ref": "scope:new", "approval_ref": "approval:mover"}
    if action == "leaver":
        for name in ("authentik", "mattermost_sessions", "mattermost_memberships"):
            revocation[name] = {
                "status": "revoked",
                "completed_at_utc": "2026-09-28T09:00:00Z",
                "evidence_ref": f"evidence:{name}",
                "not_applicable_reason_ref": None,
            }
        revocation["owned_tokens"] = {
            "status": "not_applicable",
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": "evidence:no-owned-tokens",
        }
        revocation["evidence_ref"] = "evidence:leaver-closeout"
    return {
        "event_id": f"event:{action}",
        "action": action,
        "subject_ref": "synthetic:person-001",
        "occurred_at_utc": "2026-09-28T08:00:00Z",
        "operator_ref": "actor:operator",
        "approver_ref": "actor:founder",
        "verifier_ref": "actor:reviewer",
        "role_mapping": {
            "authentik_group": "ak-kod-app-contractor-readonly",
            "mapping_evidence_ref": "evidence:role-map",
            "approved_by_ref": "actor:founder",
            "approved_at_utc": "2026-09-27T08:00:00Z",
            "resource_scope_ref": "scope:project-alpha",
        },
        "mfa": {
            "status": "verified_enrolled",
            "evidence_ref": "evidence:mfa",
            "verified_at_utc": "2026-09-28T07:55:00Z",
        },
        "mattermost": {
            "team_ref": "team:core",
            "private_channel_refs": ["channel:project-alpha-contractors"],
            "private_channels_verified": True,
            "privacy_evidence_ref": "evidence:mm-private",
            "membership_evidence_ref": "evidence:mm-membership",
        },
        "access": {
            "starts_at_utc": "2026-09-28T08:00:00Z",
            "expires_at_utc": "2026-10-28T08:00:00Z",
            "expiry_evidence_ref": "evidence:expiry",
        },
        "change": change,
        "revocation": revocation,
    }


def bundle() -> dict:
    return {
        "schema_version": 1,
        "policy": {
            "hours_threshold": 8,
            "unit": "hours/week",
            "comparator": "gt",
            "measurement_window_weeks": 4,
            "approved_by_ref": "actor:founder",
            "approved_at_utc": "2026-08-22T08:00:00Z",
            "decision_ref": "decision:founder-hours-trigger",
        },
        "trigger": {
            "measured_hours_per_week": 9,
            "period_start": "2026-08-23T00:00:00Z",
            "period_end": "2026-09-20T00:00:00Z",
            "source_evidence_ref": "evidence:weekly-hours-reviewed",
            "reviewer_ref": "actor:founder",
            "reviewed_at_utc": "2026-09-21T08:00:00Z",
        },
        "events": [event()],
    }


def test_complete_synthetic_joiner_evidence_is_contract_valid_not_provisioned() -> None:
    assert G5.validate_bundle(bundle()) == {
        "schema_version": 1,
        "trigger_exceeded": True,
        "events_validated": 1,
        "result": "evidence_contract_valid_only",
    }


@pytest.mark.parametrize("action", [None, 1, [], {}], ids=["null", "number", "list", "object"])
def test_non_string_action_refuses_cleanly(action: object) -> None:
    data = bundle()
    data["events"][0]["action"] = action

    with pytest.raises(G5.InputError, match=r"events\[0\]\.action must be joiner, mover, or leaver"):
        G5.validate_bundle(data)


@pytest.mark.parametrize(
    "bad_surface",
    ["unexpected-scalar", "unexpected-list", "unexpected-key", "non-empty-value"],
)
def test_non_leaver_requires_exact_empty_revocation_surface(bad_surface: str) -> None:
    data = bundle()
    revocation = data["events"][0]["revocation"]
    if bad_surface == "unexpected-scalar":
        revocation["authentik"] = "unexpected"
    elif bad_surface == "unexpected-list":
        revocation["authentik"] = []
    elif bad_surface == "unexpected-key":
        revocation["authentik"]["comment"] = None
    else:
        revocation["authentik"]["status"] = "revoked"

    with pytest.raises(G5.InputError, match="revocation must be empty"):
        G5.validate_bundle(data)


@pytest.mark.parametrize("action", [None, [], {}], ids=["null", "list", "object"])
def test_cli_non_string_action_refuses_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: object
) -> None:
    data = bundle()
    data["events"][0]["action"] = action
    payload = tmp_path / "non-string-action.json"
    payload.write_text(json.dumps(data), encoding="utf-8")

    assert G5.main([str(payload)]) == 2
    error = capsys.readouterr().err
    assert "action must be joiner, mover, or leaver" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["policy"].update(hours_threshold=None), "unconfigured"),
        (lambda data: data["policy"].update(hours_threshold=float("nan")), "positive founder-approved"),
        (lambda data: data["trigger"].update(period_start="2026-08-24T00:00:00Z"), "exactly match"),
        (lambda data: data["trigger"].update(measured_hours_per_week=8), "not been exceeded"),
        (lambda data: data["events"][0]["role_mapping"].update(authentik_group=None), "exact Authentik group"),
        (lambda data: data["events"][0]["role_mapping"].update(authentik_group="ak-platform-admin"), "privileged"),
        (lambda data: data["events"][0]["access"].update(expires_at_utc=None), "canonical UTC timestamp"),
        (lambda data: data["events"][0]["access"].update(expiry_evidence_ref=None), "opaque evidence reference"),
        (lambda data: data["events"][0]["mfa"].update(status="not-enrolled"), "verified_enrolled"),
        (lambda data: data["events"][0]["mattermost"].update(private_channel_refs=[]), "private_channel_refs"),
        (lambda data: data["events"][0]["mattermost"].update(private_channels_verified=False), "must be true"),
    ],
    ids=[
        "unset-threshold",
        "non-finite-threshold",
        "wrong-trigger-window",
        "hours-not-over",
        "missing-role",
        "privileged-role",
        "missing-expiry",
        "missing-expiry-proof",
        "missing-mfa",
        "missing-private-channel",
        "private-channel-not-verified",
    ],
)
def test_joiner_missing_or_unsafe_evidence_refuses(mutate, message: str) -> None:
    data = bundle()
    mutate(data)
    with pytest.raises(G5.InputError, match=message):
        G5.validate_bundle(data)


def _backdate_joiner(data: dict, when: str, *, mapping_approved: str | None = None) -> None:
    joiner = data["events"][0]
    joiner["occurred_at_utc"] = when
    joiner["access"]["starts_at_utc"] = when
    joiner["mfa"]["verified_at_utc"] = when
    joiner["role_mapping"]["approved_at_utc"] = mapping_approved or when


def test_reviewer_reproduction_joiner_before_policy_and_trigger_is_refused() -> None:
    data = bundle()
    _backdate_joiner(data, "2026-01-10T08:00:00Z", mapping_approved="2026-01-09T08:00:00Z")
    with pytest.raises(G5.InputError, match="trigger review"):
        G5.validate_bundle(data)


def test_joiner_event_before_trigger_review_is_refused() -> None:
    data = bundle()
    _backdate_joiner(data, "2026-09-21T07:59:59Z", mapping_approved="2026-09-01T08:00:00Z")
    with pytest.raises(G5.InputError, match="trigger review"):
        G5.validate_bundle(data)


def test_joiner_access_start_before_trigger_review_is_refused() -> None:
    data = bundle()
    data["events"][0]["access"]["starts_at_utc"] = "2026-09-21T07:00:00Z"
    data["events"][0]["mfa"]["verified_at_utc"] = "2026-09-21T06:00:00Z"
    data["events"][0]["role_mapping"]["approved_at_utc"] = "2026-09-21T05:00:00Z"
    with pytest.raises(G5.InputError, match="trigger review"):
        G5.validate_bundle(data)


def test_joiner_role_mapping_approved_before_policy_approval_is_refused() -> None:
    data = bundle()
    data["events"][0]["role_mapping"]["approved_at_utc"] = "2026-08-21T08:00:00Z"
    with pytest.raises(G5.InputError, match="policy approval"):
        G5.validate_bundle(data)


def test_joiner_exactly_at_trigger_review_and_policy_approval_boundaries_is_valid() -> None:
    data = bundle()
    _backdate_joiner(data, "2026-09-21T08:00:00Z", mapping_approved="2026-08-22T08:00:00Z")
    assert G5.validate_bundle(data)["result"] == "evidence_contract_valid_only"


def test_mover_requires_old_and_new_scope_and_explicit_approval() -> None:
    data = bundle()
    data["events"] = [event("mover")]
    assert G5.validate_bundle(data)["events_validated"] == 1
    data["events"][0]["change"]["approval_ref"] = None
    with pytest.raises(G5.InputError, match="opaque evidence reference"):
        G5.validate_bundle(data)


@pytest.mark.parametrize("surface", ["authentik", "mattermost_sessions", "mattermost_memberships", "owned_tokens"])
def test_leaver_requires_revocation_or_evidenced_not_applicable_for_each_surface(surface: str) -> None:
    data = bundle()
    data["events"] = [event("leaver")]
    assert G5.validate_bundle(data)["events_validated"] == 1
    data["events"][0]["revocation"][surface] = {
        "status": None,
        "completed_at_utc": None,
        "evidence_ref": None,
        "not_applicable_reason_ref": None,
    }
    with pytest.raises(
        G5.InputError, match="must prove revocation|must prove revoked or evidence-backed not_applicable"
    ):
        G5.validate_bundle(data)


def test_leaver_rejects_revocation_without_timestamp_or_evidence() -> None:
    data = bundle()
    data["events"] = [event("leaver")]
    data["events"][0]["revocation"]["authentik"]["evidence_ref"] = None
    with pytest.raises(G5.InputError, match="opaque evidence reference"):
        G5.validate_bundle(data)


def test_leaver_rejects_revocation_evidence_that_predates_departure() -> None:
    data = bundle()
    data["events"] = [event("leaver")]
    data["events"][0]["revocation"]["authentik"]["completed_at_utc"] = "2026-09-28T07:00:00Z"
    with pytest.raises(G5.InputError, match="must not predate the leaver event"):
        G5.validate_bundle(data)


@pytest.mark.parametrize("surface", ["authentik", "mattermost_memberships"])
def test_leaver_cannot_skip_account_or_channel_membership_revocation(surface: str) -> None:
    data = bundle()
    data["events"] = [event("leaver")]
    data["events"][0]["revocation"][surface] = {
        "status": "not_applicable",
        "completed_at_utc": None,
        "evidence_ref": None,
        "not_applicable_reason_ref": "evidence:no-access",
    }
    with pytest.raises(G5.InputError, match="must prove revocation"):
        G5.validate_bundle(data)


def test_leaver_closeout_does_not_depend_on_hiring_hours_trigger() -> None:
    data = bundle()
    data["policy"]["hours_threshold"] = None
    data["trigger"] = None
    data["events"] = [event("leaver")]
    result = G5.validate_bundle(data)
    assert result["trigger_exceeded"] is False
    assert result["events_validated"] == 1


def test_cli_refuses_checked_in_unconfigured_founder_decision(tmp_path, capsys) -> None:
    example = SCRIPT.parents[1] / "runbooks/examples/contractor-onboarding-unconfigured.synthetic.json"
    assert G5.main([str(example)]) == 2
    assert "threshold is unconfigured" in capsys.readouterr().err


def test_cli_rejects_duplicate_keys_and_oversized_input(tmp_path, capsys) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert G5.main([str(duplicate)]) == 2
    assert "duplicate JSON key" in capsys.readouterr().err
    large = tmp_path / "large.json"
    large.write_bytes(b" " * (G5._MAX_BYTES + 1))
    assert G5.main([str(large)]) == 2
    assert "must not exceed" in capsys.readouterr().err


def test_validator_has_no_network_database_or_process_dependencies() -> None:
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})
