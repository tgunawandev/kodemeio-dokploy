from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from affiliate import InputError, evaluate, load_document, reconcile  # noqa: E402

SCHEMA = ROOT / "contracts" / "affiliate.v1.schema.json"
SAMPLE = ROOT / "examples" / "affiliate.synthetic.v1.json"
TEMPLATE = ROOT / "examples" / "affiliate.template.v1.json"


def payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_schema_and_synthetic_candidate_are_valid_but_not_operational() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload())
    report = evaluate(payload())
    assert report["status"] == "candidate-ready-unverified"
    assert report["verified"] is False
    assert report["live_integration"] is False


def test_template_fails_closed_until_approval_and_disclosure_are_supplied() -> None:
    report = evaluate(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    assert report["status"] == "blocked"
    assert any("approval" in issue for issue in report["issues"])
    assert any("disclosure" in issue for issue in report["issues"])


@pytest.mark.parametrize("mutation", ["approval", "terms_expiry", "link_expiry", "disclosure"])
def test_missing_or_expired_approval_disclosure_and_terms_block(mutation: str) -> None:
    doc = payload()
    if mutation == "approval":
        doc["advertiser"]["approval"]["status"] = "pending"
    elif mutation == "terms_expiry":
        doc["advertiser"]["commission_terms"]["valid_until"] = "2026-09-27"
    elif mutation == "link_expiry":
        doc["links"][0]["expires_on"] = "2026-09-27"
    else:
        doc["links"][0]["disclosure"] = " "
    assert evaluate(doc)["status"] == "blocked"


@pytest.mark.parametrize(
    "destination",
    [
        "http://shop.example.test/item/1",
        "https://evil.example.test/item/1",
        "https://shop.example.test.evil.example/item/1",
        "https://user@shop.example.test/item/1",
        "https://127.0.0.1/item/1",
    ],
)
def test_bad_destination_fails_closed(destination: str) -> None:
    doc = payload()
    doc["links"][0]["destination_url"] = destination
    report = evaluate(doc)
    assert report["status"] == "blocked"
    assert "links[0]:destination_not_allowlisted" in report["issues"]


def test_expired_campaign_and_inactive_link_cannot_reconcile() -> None:
    doc = payload()
    doc["as_of"] = "2027-01-01"
    assert evaluate(doc)["status"] == "blocked"
    doc = payload()
    doc["links"][0]["status"] = "paused"
    assert evaluate(doc)["status"] == "blocked"


def test_event_expiry_and_commission_order_are_enforced() -> None:
    doc = payload()
    doc["links"][0]["expires_on"] = "2026-09-19"
    assert "events[0]:after_link_expiry" in evaluate(doc)["issues"]
    doc = payload()
    doc["events"][1]["occurred_at"] = "2026-09-19T12:00:00Z"
    assert "events[1]:commission_precedes_click" in evaluate(doc)["issues"]


def test_attribution_snapshot_hash_is_immutable() -> None:
    doc = payload()
    doc["links"][0]["attribution"]["campaign"] = "changed"
    report = evaluate(doc)
    assert report["status"] == "blocked"
    assert "links[0]:attribution_hash_mismatch" in report["issues"]


def test_reconciliation_is_idempotent_and_commission_math_is_evidence_only() -> None:
    doc = payload()
    result = reconcile(doc)
    repeated = reconcile(copy.deepcopy(doc))
    assert result == repeated
    assert result["status"] == "synthetic-evidence-only"
    assert result["click_count"] == 1
    assert result["commission_count"] == 1
    assert result["commission_total_minor"] == 500
    assert result["payments_created"] is False


def test_exact_duplicate_synthetic_events_are_deduplicated() -> None:
    doc = payload()
    doc["events"].append(copy.deepcopy(doc["events"][0]))
    doc["events"].append(copy.deepcopy(doc["events"][1]))
    result = reconcile(doc)
    assert result["click_count"] == 1
    assert result["commission_count"] == 1
    assert result["duplicate_event_count"] == 2


def test_conflicting_duplicate_event_id_fails_closed() -> None:
    doc = payload()
    conflicting = copy.deepcopy(doc["events"][0])
    conflicting["attribution"]["source"] = "forged"
    doc["events"].append(conflicting)
    with pytest.raises(InputError, match="duplicate_event_conflict"):
        reconcile(doc)


def test_commission_mismatch_or_orphan_click_is_rejected() -> None:
    doc = payload()
    doc["events"][1]["commission_minor"] = 501
    with pytest.raises(InputError, match="commission_amount_mismatch"):
        reconcile(doc)
    doc = payload()
    doc["events"][1]["click_event_id"] = "missing-click"
    with pytest.raises(InputError, match="commission_click_missing"):
        reconcile(doc)


def test_cli_emits_synthetic_evidence_and_duplicate_json_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "affiliate.py"), "reconcile", str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["payments_created"] is False
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate_json_key"):
        load_document(duplicate)
