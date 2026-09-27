from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teracorp_g7_data_map import InputError, evaluate, load_document  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SCHEMA = ROOT / "contracts" / "teracorp_g7_data_map.v1.schema.json"
VALID = EXAMPLES / "teracorp_g7_data_map_complete_unreviewed.synthetic.v1.json"


def valid_payload() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def assert_incomplete(payload: dict, issue: str) -> None:
    report = evaluate(payload)
    assert report["status"] == "incomplete"
    assert issue in report["issues"]
    assert "compliant" not in json.dumps(report).lower()


def test_synthetic_full_shape_is_schema_valid_but_only_for_counsel_review() -> None:
    payload = valid_payload()
    errors = list(
        Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=FormatChecker()).iter_errors(payload)
    )
    assert not errors
    report = evaluate(payload)
    assert report == {
        "status": "complete-for-counsel-review-unverified",
        "issues": [],
        "verified": False,
        "legal_reviewed": False,
    }


def test_blank_generic_template_is_not_a_product_assessment() -> None:
    template = json.loads((EXAMPLES / "teracorp_g7_data_map.template.v1.json").read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=FormatChecker()).iter_errors(template)
    )
    assert not errors
    report = evaluate(template)
    assert report["status"] == "incomplete"
    assert "sections.purposes:missing" in report["issues"]
    assert report["verified"] is False


def test_missing_required_sections_blocks_completeness() -> None:
    payload = valid_payload()
    del payload["sections"]["access_roles"]
    assert_incomplete(payload, "sections.access_roles:missing")


@pytest.mark.parametrize(
    ("section", "decision", "expected_issue"),
    [
        ("lawful_basis", "unresolved", "sections.lawful_basis:decision_unresolved"),
        ("consent", "unresolved", "sections.consent:decision_unresolved"),
    ],
)
def test_unresolved_lawful_basis_or_consent_prevents_readiness(
    section: str, decision: str, expected_issue: str
) -> None:
    payload = valid_payload()
    payload["sections"][section]["decision"] = decision
    assert_incomplete(payload, expected_issue)


def test_processor_contract_gap_is_explicit_and_incomplete() -> None:
    payload = valid_payload()
    payload["sections"]["processors"]["items"][0]["terms_status"] = "unassessed"
    assert_incomplete(payload, "sections.processors.items[0]:assessment_unresolved")


def test_transfer_assessment_gap_is_incomplete() -> None:
    payload = valid_payload()
    residency = payload["sections"]["residency_transfers"]
    residency["no_transfers_declared"] = False
    residency["transfers"] = [
        {
            "transfer_id": "synthetic-transfer",
            "destination": "Synthetic destination",
            "mechanism_description": "Synthetic mechanism",
            "assessment_status": "unassessed",
            "evidence_ref": "synthetic-evidence",
        }
    ]
    assert_incomplete(payload, "sections.residency_transfers.transfers[0]:assessment_unresolved")


def test_retention_gap_is_incomplete() -> None:
    payload = valid_payload()
    payload["sections"]["retention"]["items"][0]["decision_status"] = "unresolved"
    assert_incomplete(payload, "sections.retention.items[0]:decision_unresolved")


@pytest.mark.parametrize("field", ["processing_operations", "processors", "deletion_test"])
def test_cross_product_scope_is_rejected(field: str) -> None:
    payload = valid_payload()
    if field == "deletion_test":
        payload["sections"][field]["product_id"] = "B2"
        expected = "sections.deletion_test:cross_product_scope"
    else:
        payload["sections"][field]["items"][0]["product_id"] = "B2"
        expected = f"sections.{field}.items[0]:cross_product_scope"
    assert_incomplete(payload, expected)


def test_missing_deletion_test_evidence_is_incomplete() -> None:
    payload = valid_payload()
    del payload["sections"]["deletion_test"]
    assert_incomplete(payload, "sections.deletion_test:missing")


def test_deletion_test_with_mismatched_observation_is_incomplete() -> None:
    payload = valid_payload()
    payload["sections"]["deletion_test"]["observed_result"] = "Synthetic records remain after test deletion"
    assert_incomplete(payload, "sections.deletion_test:expected_observed_mismatch")


def test_stale_deletion_test_evidence_is_incomplete() -> None:
    payload = valid_payload()
    payload["sections"]["deletion_test"]["valid_until"] = "2024-01-01"
    assert_incomplete(payload, "sections.deletion_test:stale_evidence")


def test_empty_processor_inventory_requires_explicit_no_processor_assessment() -> None:
    payload = valid_payload()
    section = payload["sections"]["processors"]
    section["no_processors_declared"] = True
    section["items"] = []
    assert evaluate(payload)["status"] == "complete-for-counsel-review-unverified"
    section["no_processors_declared"] = False
    assert_incomplete(payload, "sections.processors:processor_inventory_empty")


def test_unreviewed_state_cannot_be_promoted_by_input() -> None:
    payload = valid_payload()
    assert payload["counsel_review"]["status"] == "pending"
    payload["counsel_review"]["status"] = "approved"
    assert evaluate(payload)["status"] == "incomplete"


def test_unknown_keys_are_strict_and_output_never_echoes_submitted_text() -> None:
    payload = valid_payload()
    payload["secret_customer_note"] = "synthetic-canary-no-real-data"
    report = evaluate(payload)
    assert report["status"] == "incomplete"
    assert "synthetic-canary-no-real-data" not in json.dumps(report)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate_json_key"):
        load_document(path)


def test_oversize_input_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "oversize.json"
    path.write_bytes(b" " * 1_000_001)
    with pytest.raises(InputError, match="input_too_large"):
        load_document(path)


def test_cli_sample_outputs_only_unverified_review_state() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "teracorp_g7_data_map.py"), str(VALID)],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["status"] == "complete-for-counsel-review-unverified"
    assert report["verified"] is False and report["legal_reviewed"] is False
    assert "compliant" not in result.stdout.lower()


def test_structural_invalid_types_return_safe_incomplete_report() -> None:
    assert evaluate(["not", "an", "object"])["status"] == "incomplete"
