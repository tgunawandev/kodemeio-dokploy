from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "teracorp_contractor_readiness.py"
SPEC = importlib.util.spec_from_file_location("teracorp_contractor_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
G5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G5)


def valid_bundle() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy": {
            "hours_threshold": 8,
            "unit": "hours/week",
            "comparator": "gt",
            "measurement_window_weeks": 4,
            "approved_by_ref": "actor:founder",
            "approved_at_utc": "2026-08-22T08:00:00Z",
            "decision_ref": "decision:hours-trigger",
        },
        "trigger": {
            "measured_hours_per_week": 9,
            "period_start": "2026-08-23T00:00:00Z",
            "period_end": "2026-09-20T00:00:00Z",
            "source_evidence_ref": "evidence:hours",
            "reviewer_ref": "actor:founder",
            "reviewed_at_utc": "2026-09-21T08:00:00Z",
        },
        "events": [
            {
                "event_id": "event:joiner",
                "action": "joiner",
                "subject_ref": "synthetic:person-1",
                "occurred_at_utc": "2026-09-28T08:00:00Z",
                "operator_ref": "actor:operator",
                "approver_ref": "actor:founder",
                "verifier_ref": "actor:reviewer",
                "role_mapping": {
                    "authentik_group": "ak-project-readonly",
                    "mapping_evidence_ref": "evidence:role-mapping",
                    "approved_by_ref": "actor:founder",
                    "approved_at_utc": "2026-09-27T08:00:00Z",
                    "resource_scope_ref": "scope:project",
                },
                "mfa": {
                    "status": "verified_enrolled",
                    "evidence_ref": "evidence:mfa",
                    "verified_at_utc": "2026-09-28T07:55:00Z",
                },
                "mattermost": {
                    "team_ref": "team:teracorp",
                    "private_channel_refs": ["channel:project"],
                    "private_channels_verified": True,
                    "privacy_evidence_ref": "evidence:privacy",
                    "membership_evidence_ref": "evidence:membership",
                },
                "access": {
                    "starts_at_utc": "2026-09-28T08:00:00Z",
                    "expires_at_utc": "2026-10-28T08:00:00Z",
                    "expiry_evidence_ref": "evidence:expiry",
                },
                "change": {"old_scope_ref": None, "new_scope_ref": None, "approval_ref": None},
                "revocation": {
                    "authentik": None,
                    "mattermost_sessions": None,
                    "mattermost_memberships": None,
                    "owned_tokens": None,
                    "evidence_ref": None,
                },
            }
        ],
    }


def test_cli_rejects_json_nesting_above_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = tmp_path / "deep.json"
    payload.write_text("{" * (G5._MAX_JSON_NESTING + 1) + "0" + "}" * (G5._MAX_JSON_NESTING + 1), encoding="utf-8")

    assert G5.main([str(payload)]) == 2
    assert f"nesting must not exceed {G5._MAX_JSON_NESTING} levels" in capsys.readouterr().err


def test_brackets_inside_json_string_do_not_count_as_nesting() -> None:
    raw = json.dumps({"text": "[]{}" * (G5._MAX_JSON_NESTING * 10)})

    G5._check_json_nesting(raw)


def test_cli_maps_parser_recursion_error_to_sanitized_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "parser-failure.json"
    payload.write_text("{}", encoding="utf-8")

    def raise_recursion_error(*args: object, **kwargs: object) -> object:
        raise RecursionError("internal parser details")

    monkeypatch.setattr(G5.json, "loads", raise_recursion_error)
    assert G5.main([str(payload)]) == 2
    error = capsys.readouterr().err
    assert "input JSON exceeds parser safety limits" in error
    assert "internal parser details" not in error


@pytest.mark.parametrize(
    ("approved_at", "occurred_at", "access_starts"),
    [
        ("2026-09-28T08:01:00Z", "2026-09-28T08:00:00Z", "2026-09-28T08:02:00Z"),
        ("2026-09-28T08:01:00Z", "2026-09-28T08:02:00Z", "2026-09-28T08:00:00Z"),
    ],
)
def test_role_approval_must_precede_event_and_access_start(
    approved_at: str, occurred_at: str, access_starts: str
) -> None:
    payload = valid_bundle()
    role_mapping = payload["events"][0]["role_mapping"]  # type: ignore[index]
    role_mapping["approved_at_utc"] = approved_at

    with pytest.raises(G5.InputError, match="approval must precede"):
        G5._mapping(
            role_mapping,
            "actor:founder",
            G5._utc(occurred_at, "test.event"),
            G5._utc(access_starts, "test.access"),
        )


def test_role_approval_must_match_event_approver_claim() -> None:
    payload = valid_bundle()
    role_mapping = payload["events"][0]["role_mapping"]  # type: ignore[index]
    role_mapping["approved_by_ref"] = "actor:unrelated"

    with pytest.raises(G5.InputError, match="must match the lifecycle event approver_ref"):
        G5.validate_bundle(payload)


@pytest.mark.parametrize(
    "claim_path",
    [
        ("mfa", "evidence_ref"),
        ("mattermost", "privacy_evidence_ref"),
        ("mattermost", "membership_evidence_ref"),
        ("access", "expiry_evidence_ref"),
    ],
)
def test_role_mapping_evidence_cannot_be_reused_for_other_claims(claim_path: tuple[str, str]) -> None:
    payload = valid_bundle()
    event = payload["events"][0]  # type: ignore[index]
    event["role_mapping"]["mapping_evidence_ref"] = event[claim_path[0]][claim_path[1]]

    with pytest.raises(G5.InputError, match="must identify role-mapping evidence only"):
        G5.validate_bundle(payload)


def test_cli_reads_at_most_max_bytes_plus_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[int] = []

    class BoundedReader:
        def __enter__(self) -> BoundedReader:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            requested.append(size)
            return b"x" * size

    monkeypatch.setattr(G5.Path, "open", lambda *args, **kwargs: BoundedReader())
    assert G5.main([str(tmp_path / "oversized.json")]) == 2
    assert requested == [G5._MAX_BYTES + 1]
    assert f"input must not exceed {G5._MAX_BYTES} bytes" in capsys.readouterr().err


def test_cli_sanitizes_huge_json_integer_without_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = tmp_path / "huge-integer.json"
    payload.write_text('{"value":' + "9" * 5_000 + "}", encoding="utf-8")

    assert G5.main([str(payload)]) == 2
    error = capsys.readouterr().err
    assert "input JSON exceeds parser safety limits" in error
    assert "Traceback" not in error
    assert "ValueError" not in error


def test_cli_duplicate_key_error_does_not_echo_untrusted_key_or_controls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "duplicate-control-key.json"
    untrusted_key = "\x1b[2JSECRET"
    encoded_key = json.dumps(untrusted_key)
    payload.write_text("{" + encoded_key + ":1," + encoded_key + ":2}", encoding="utf-8")

    assert G5.main([str(payload)]) == 2
    error = capsys.readouterr().err
    assert "duplicate JSON key" in error
    assert untrusted_key not in error
    assert "SECRET" not in error
    assert "\x1b" not in error
    assert "Traceback" not in error
