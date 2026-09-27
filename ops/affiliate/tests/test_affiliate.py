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
import affiliate  # noqa: E402
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
    assert report["manual_review_required"] is True
    assert report["approval_authenticated"] is False
    assert report["consent_captured"] is False
    assert report["consent_authenticated"] is False
    assert report["as_of_authenticated"] is False


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
    assert result["manual_review_required"] is True
    assert result["approval_authenticated"] is False
    assert result["consent_captured"] is False
    assert result["consent_authenticated"] is False


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


@pytest.mark.parametrize(
    "order_amount,rate_bps,commission",
    [(1, 5000, 1), (1, 4999, 0)],
)
def test_commission_rounding_uses_explicit_half_up_minor_unit_rule(order_amount, rate_bps, commission):
    doc = payload()
    doc["advertiser"]["commission_terms"]["rate_bps"] = rate_bps
    doc["events"][1]["order_amount_minor"] = order_amount
    doc["events"][1]["commission_minor"] = commission
    assert evaluate(doc)["status"] == "candidate-ready-unverified"


def test_click_attribution_must_match_the_link_snapshot():
    doc = payload()
    doc["events"][0]["attribution"]["campaign"] = "altered-campaign"
    assert "events[0]:attribution_mismatch" in evaluate(doc)["issues"]


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


def test_loader_rejects_json_nesting_beyond_configured_depth(tmp_path: Path) -> None:
    too_deep = tmp_path / "too-deep.json"
    too_deep.write_text("[" * (affiliate.MAX_JSON_DEPTH + 1) + "0" + "]" * (affiliate.MAX_JSON_DEPTH + 1))

    with pytest.raises(InputError, match="input_too_deep"):
        load_document(too_deep)


def test_loader_ignores_brackets_inside_json_strings(tmp_path: Path) -> None:
    bracket_heavy = tmp_path / "bracket-heavy-string.json"
    value = "[]{}" * (affiliate.MAX_JSON_DEPTH * 4)
    bracket_heavy.write_text(json.dumps({"value": value}), encoding="utf-8")

    assert load_document(bracket_heavy) == {"value": value}


def test_loader_normalizes_recursion_error_to_sanitized_input_error(tmp_path: Path, monkeypatch) -> None:
    document = tmp_path / "document.json"
    document.write_text("{}", encoding="utf-8")

    def raise_recursion_error(*args, **kwargs):
        raise RecursionError("untrusted implementation detail")

    monkeypatch.setattr(affiliate.json, "loads", raise_recursion_error)
    with pytest.raises(InputError, match="^input_unreadable_or_invalid_json$"):
        load_document(document)


def test_loader_rejects_oversized_integer_tokens_without_traceback(tmp_path: Path):
    document = tmp_path / "oversized-integer.json"
    document.write_text("1" * 5000, encoding="utf-8")
    with pytest.raises(InputError, match="^json_integer_out_of_range$"):
        load_document(document)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "affiliate.py"), "validate", str(document)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["manual_review_required"] is True


def test_amount_bounds_and_aggregate_total_fail_closed():
    document = payload()
    document["events"][1]["order_amount_minor"] = affiliate.MAX_AMOUNT_MINOR + 1
    assert evaluate(document)["status"] == "blocked"

    document = payload()
    document["events"][1]["commission_minor"] = affiliate.MAX_AMOUNT_MINOR + 1
    assert evaluate(document)["status"] == "blocked"

    document = payload()
    event = document["events"][1]
    event["order_amount_minor"] = affiliate.MAX_AMOUNT_MINOR
    event["commission_minor"] = affiliate.MAX_AMOUNT_MINOR
    document["events"].extend(
        [
            {
                **copy.deepcopy(event),
                "event_id": f"commission-{index:04d}",
                "click_event_id": document["events"][0]["event_id"],
                "commission_minor": affiliate.MAX_AMOUNT_MINOR,
            }
            for index in range(2)
        ]
    )
    assert "events:aggregate_commission_amount_out_of_range" in evaluate(document)["issues"]


def test_exact_per_event_and_aggregate_amount_limit_is_inclusive():
    document = payload()
    document["advertiser"]["commission_terms"]["rate_bps"] = 10000
    document["events"][1]["order_amount_minor"] = affiliate.MAX_AMOUNT_MINOR
    document["events"][1]["commission_minor"] = affiliate.MAX_TOTAL_MINOR
    report = evaluate(document)
    assert report["status"] == "candidate-ready-unverified"
    assert report["issues"] == []


def test_backdated_snapshot_is_always_explicitly_unauthenticated():
    document = payload()
    document["as_of"] = "2026-09-27"
    report = evaluate(document)
    assert report["status"] == "candidate-ready-unverified"
    assert report["manual_review_required"] is True
    assert report["approval_authenticated"] is False
    assert report["consent_authenticated"] is False
    assert report["as_of_authenticated"] is False
