from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teracorp_g6_monthly_evidence import InputError, evaluate, load_document  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "teracorp_g6_monthly_evidence.v1.schema.json"
SAMPLE = ROOT / "examples" / "teracorp_g6_monthly_evidence.synthetic.v1.json"
AS_OF = "2026-09-28T00:00:00Z"


def valid_payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def assert_incomplete(payload: dict, issue: str, as_of: str = AS_OF) -> None:
    report = evaluate(payload, as_of)
    assert report["status"] == "incomplete"
    assert issue in report["issues"]
    assert report["verified"] is False and report["legal_reviewed"] is False


def add_incident_decision(payload: dict) -> None:
    decision = copy.deepcopy(payload["decisions"][-1])
    decision.update(
        decision_id="d-incident-synthetic-1",
        topic="incident_exception_remediation",
        scope_type="incident",
        scope_id="incident-synthetic-1",
    )
    payload["decisions"].append(decision)
    payload["incident_inventory"]["items"].append(
        {
            "incident_id": "incident-synthetic-1",
            "handling_status": "handled",
            "evidence_refs": ["ev.synthetic.incident.one"],
            "decision_id": decision["decision_id"],
        }
    )


def test_synthetic_package_is_structural_inventory_only_and_schema_valid() -> None:
    payload = valid_payload()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
    assert evaluate(payload, AS_OF) == {
        "status": "complete-for-counsel-review-unverified",
        "issues": [],
        "verified": False,
        "legal_reviewed": False,
        "inventory_counts": {"decisions": 8, "change_items": 2, "incident_items": 0},
    }


def test_missing_required_decision_blocks_completeness() -> None:
    payload = valid_payload()
    payload["decisions"] = [d for d in payload["decisions"] if d["topic"] != "pdp_data_protection"]
    assert_incomplete(payload, "decisions.pdp_data_protection:missing_month_decision")


def test_expired_decision_blocks_completeness() -> None:
    payload = valid_payload()
    payload["decisions"][0]["valid_until"] = "2026-09-27T23:59:59Z"
    assert_incomplete(payload, "decisions[0]:decision_expired")


def test_future_decision_blocks_completeness() -> None:
    payload = valid_payload()
    payload["decisions"][0]["issued_at"] = "2026-09-29T00:00:00Z"
    payload["decisions"][0]["valid_from"] = "2026-09-29T00:00:00Z"
    assert_incomplete(payload, "decisions[0]:decision_future")


@pytest.mark.parametrize("subject_type", ["product", "processor"])
def test_changed_product_or_processor_without_exact_scope_decision_is_incomplete(subject_type: str) -> None:
    payload = valid_payload()
    item = next(row for row in payload["change_inventory"]["items"] if row["subject_type"] == subject_type)
    item["change_status"] = "changed"
    item["decision_id"] = "missing-scoped-decision"
    assert_incomplete(
        payload, f"change_inventory.items[{payload['change_inventory']['items'].index(item)}]:decision_uncovered"
    )


def test_unresolved_ojk_regulatory_scope_blocks_completeness() -> None:
    payload = valid_payload()
    next(d for d in payload["decisions"] if d["topic"] == "ojk_regulatory_boundary")["status"] = "unresolved"
    assert_incomplete(payload, "decisions.ojk_regulatory_boundary:decision_unresolved")


def test_unhandled_incident_blocks_completeness() -> None:
    payload = valid_payload()
    add_incident_decision(payload)
    payload["incident_inventory"]["items"][0]["handling_status"] = "unhandled"
    assert_incomplete(payload, "incident_inventory.items[0]:incident_unhandled")


def test_unknown_changed_status_and_unknown_inventories_block() -> None:
    payload = valid_payload()
    payload["change_inventory"]["inventory_status"] = "unknown"
    payload["change_inventory"]["items"][0]["change_status"] = "unknown"
    payload["incident_inventory"]["inventory_status"] = "unknown"
    report = evaluate(payload, AS_OF)
    assert report["status"] == "incomplete"
    assert "change_inventory:inventory_unknown" in report["issues"]
    assert "change_inventory.items[0]:change_status_unknown" in report["issues"]
    assert "incident_inventory:inventory_unknown" in report["issues"]


def test_naive_decision_timestamp_is_rejected() -> None:
    payload = valid_payload()
    payload["decisions"][0]["issued_at"] = "2026-09-20T10:00:00"
    assert evaluate(payload, AS_OF)["status"] == "incomplete"


def test_invalid_shape_unknown_keys_and_non_echoing_output() -> None:
    payload = valid_payload()
    payload["untrusted-extra"] = "synthetic-no-echo-canary"
    report = evaluate(payload, AS_OF)
    assert report["status"] == "incomplete"
    assert "synthetic-no-echo-canary" not in json.dumps(report)
    assert "untrusted-extra" not in json.dumps(report)


def test_malformed_types_return_incomplete_without_crashing() -> None:
    report = evaluate({"schema_version": 1, "month": "2026-09", "decisions": None}, AS_OF)
    assert report["status"] == "incomplete"
    assert report["inventory_counts"] == {"decisions": 0, "change_items": 0, "incident_items": 0}


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate_json_key"):
        load_document(path)


def test_oversized_input_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * 262_145)
    with pytest.raises(InputError, match="input_too_large"):
        load_document(path)


def test_malformed_json_is_rejected_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text('{"secret-marker":"synthetic-no-echo",}', encoding="utf-8")
    with pytest.raises(InputError, match="input_unreadable_or_invalid_json"):
        load_document(path)


def test_output_is_deterministic_and_never_reports_approval() -> None:
    payload = valid_payload()
    first = evaluate(payload, AS_OF)
    second = evaluate(payload, AS_OF)
    assert first == second
    assert first["status"] in {"incomplete", "complete-for-counsel-review-unverified"}
    assert "compliant" not in json.dumps(first).lower()


def test_cli_requires_explicit_as_of_and_reports_only_unverified_inventory_state() -> None:
    script = Path(__file__).resolve().parents[1] / "teracorp_g6_monthly_evidence.py"
    result = subprocess.run(
        [sys.executable, str(script), "--as-of", AS_OF, str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["status"] == "complete-for-counsel-review-unverified"
    assert report["verified"] is False and report["legal_reviewed"] is False
    assert "compliant" not in result.stdout.lower()


def test_unresolved_processor_inventory_is_not_treated_as_no_processors() -> None:
    payload = valid_payload()
    payload["change_inventory"]["processor_inventory_status"] = "unknown"
    assert_incomplete(payload, "change_inventory:processor_inventory_unknown")


def test_incident_decision_must_cover_exact_incident() -> None:
    payload = valid_payload()
    add_incident_decision(payload)
    payload["incident_inventory"]["items"][0]["decision_id"] = "d-incident"
    assert_incomplete(payload, "incident_inventory.items[0]:decision_uncovered")
