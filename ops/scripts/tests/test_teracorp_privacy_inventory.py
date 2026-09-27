from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teracorp_privacy_inventory import InputError, load, validate  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "ops/examples/teracorp_privacy_inventory.synthetic.v1.json"
SCHEMA = ROOT / "ops/contracts/teracorp_privacy_inventory.v1.schema.json"
AS_OF = "2026-09-28"


def payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_schema_and_synthetic_inventory_are_valid_but_never_verified() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload()))
    report = validate(payload(), AS_OF)
    assert report["status"] == "inventory-structured-for-founder-counsel-review-unverified"
    assert report["verified"] is False
    assert report["legal_reviewed"] is False
    assert report["deletion_verified"] is False


def test_unknown_or_asserted_states_are_not_promoted_to_complete() -> None:
    data = payload()
    data["processor_inventory_status"] = "unknown"
    data["processors"][1]["inventory_status"] = "unknown"
    product = data["products"][0]
    product.update(
        inventory_status="unknown",
        consent_status="unresolved",
        consent_evidence_refs=[],
        retention_status="unresolved",
        retention_evidence_refs=[],
        deletion_test_status="unknown",
        deletion_tested_on=None,
        deletion_evidence_refs=[],
        pia_status="unknown",
        pia_evidence_refs=[],
        data_flows=[],
    )
    report = validate(data, AS_OF)
    assert report["unresolved_count"] >= 6
    assert report["verified"] is False and report["deletion_verified"] is False
    assert "synthetic-product:inventory_status:unknown" in report["unresolved"]


def test_none_declared_processor_inventory_is_distinct_and_unverified() -> None:
    data = payload()
    data.update(processor_inventory_status="none_declared", processors=[])
    data["products"][0].update(inventory_status="unknown", data_flows=[])
    report = validate(data, AS_OF)
    assert "processor_inventory_status:none_declared_unverified" in report["unresolved"]


def test_identified_processor_requires_source_reference() -> None:
    data = payload()
    data["processors"][0]["evidence_refs"] = []
    with pytest.raises(InputError, match="non-empty"):
        validate(data, AS_OF)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["products"][0]["data_flows"][0].update(processor_id="missing"),
        lambda p: p["products"][0]["data_flows"][0].update(evidence_refs=["missing"]),
        lambda p: p["products"][0].update(consent_status="documented", consent_evidence_refs=[]),
        lambda p: p["products"][0].update(deletion_tested_on="2026-09-29"),
        lambda p: p["evidence_refs"][0].update(sha256="bad"),
        lambda p: p["products"][0]["data_flows"][0].update(data_categories=["account_profile", "account_profile"]),
    ],
)
def test_broken_references_or_inconsistent_evidence_fail_closed(mutate) -> None:
    data = payload()
    mutate(data)
    with pytest.raises(InputError):
        validate(data, AS_OF)


def test_product_and_flow_identifiers_must_be_unique() -> None:
    data = payload()
    second = copy.deepcopy(data["products"][0])
    data["products"].append(second)
    with pytest.raises(InputError, match="product_id"):
        validate(data, AS_OF)


def test_failed_deletion_test_is_reported_and_evidence_cannot_predate_test() -> None:
    data = payload()
    data["products"][0]["deletion_test_status"] = "failed"
    report = validate(data, AS_OF)
    assert "synthetic-product:deletion_test_status:failed" in report["unresolved"]
    data["evidence_refs"][4]["observed_on"] = "2026-09-23"
    with pytest.raises(InputError, match="predates"):
        validate(data, AS_OF)


def test_bad_shapes_never_crash_or_echo_untrusted_values() -> None:
    data = payload()
    data["products"][0]["consent_status"] = {"private-canary": "bad"}
    with pytest.raises(InputError):
        validate(data, AS_OF)
    malformed = {"private-canary": "bad"}
    with pytest.raises(InputError):
        validate(malformed, AS_OF)


def test_cli_duplicate_keys_oversize_and_invalid_utf8_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate_json_key"):
        load(duplicate)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 1_000_001)
    with pytest.raises(InputError, match="input_too_large"):
        load(oversized)
    invalid_utf8 = tmp_path / "invalid.json"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(InputError, match="input_unreadable_or_invalid_json"):
        load(invalid_utf8)


def test_cli_has_no_network_and_emits_only_structural_unverified_status() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "ops/scripts/teracorp_privacy_inventory.py"), "--as-of", AS_OF, str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["verified"] is False and report["legal_reviewed"] is False
    assert report["deletion_verified"] is False
    assert "compliant" not in result.stdout.lower()
