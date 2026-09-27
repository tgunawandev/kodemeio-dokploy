from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema.validators import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teracorp_hook_library import InputError, _load_input, validate_library  # noqa: E402


def base_payload() -> dict:
    return {
        "schema_version": 1,
        "as_of_date": "2026-09-28",
        "hooks": [
            {
                "hook_id": "benefit-hook",
                "version": 1,
                "brand": "terakod",
                "hook_type": "benefit",
                "variant_label": "variant-a",
                "created_on": "2026-09-01",
                "lifecycle": "active",
            }
        ],
        "evidence_refs": [
            {
                "evidence_ref_id": "evidence-one",
                "brand": "terakod",
                "evidence_kind": "performance_source",
                "verification_status": "verified",
                "sha256": "a" * 64,
                "observed_on": "2026-09-15",
            },
            {
                "evidence_ref_id": "evidence-two",
                "brand": "terakod",
                "evidence_kind": "performance_source",
                "verification_status": "verified",
                "sha256": "b" * 64,
                "observed_on": "2026-09-22",
            },
            {
                "evidence_ref_id": "approval",
                "brand": "terakod",
                "evidence_kind": "founder_approval",
                "verification_status": "verified",
                "sha256": "c" * 64,
                "observed_on": "2026-09-24",
            },
            {
                "evidence_ref_id": "threshold-approval",
                "brand": "terakod",
                "evidence_kind": "threshold_approval",
                "verification_status": "verified",
                "sha256": "d" * 64,
                "observed_on": "2026-09-24",
            },
        ],
        "performance_windows": [
            {
                "schema_version": 1,
                "window_id": "window-one",
                "hook_id": "benefit-hook",
                "hook_version": 1,
                "brand": "terakod",
                "window_start": "2026-09-08",
                "window_end": "2026-09-14",
                "observed_on": "2026-09-15",
                "provenance": "real",
                "source_id": "aggregate-source",
                "impressions": 100,
                "engagements": 10,
                "conversions": 5,
                "evidence_ref_id": "evidence-one",
            },
            {
                "schema_version": 1,
                "window_id": "window-two",
                "hook_id": "benefit-hook",
                "hook_version": 1,
                "brand": "terakod",
                "window_start": "2026-09-15",
                "window_end": "2026-09-21",
                "observed_on": "2026-09-22",
                "provenance": "real",
                "source_id": "aggregate-source",
                "impressions": 100,
                "engagements": 8,
                "conversions": 4,
                "evidence_ref_id": "evidence-two",
            },
        ],
        "retirement_decisions": [],
    }


def retirement_decision() -> dict:
    return {
        "hook_id": "benefit-hook",
        "version": 1,
        "decision": "retire",
        "decided_on": "2026-09-25",
        "approved_by": "founder-one",
        "approval_evidence_ref": "approval",
        "thresholds": {
            "minimum_real_windows": 2,
            "minimum_impressions_per_window": 50,
            "maximum_conversion_rate": "0.10",
            "threshold_evidence_ref": "threshold-approval",
        },
    }


def test_published_json_schemas_are_valid_and_synthetic_example_is_labelled():
    repo = Path(__file__).resolve().parents[3]
    for name in (
        "teracorp_hook_library.v1.schema.json",
        "teracorp_hook_performance_window.v1.schema.json",
    ):
        schema = json.loads((repo / "ops/contracts" / name).read_text())
        Draft202012Validator.check_schema(schema)
    example = json.loads((repo / "ops/examples/teracorp_hook_library.synthetic.v1.json").read_text())
    result = validate_library(example)
    assert result["performance_windows"][0]["provenance"] == "synthetic"
    assert result["performance_windows"][0]["outcome_classification"] == "synthetic_sample_not_real_outcome"
    assert result["retirement_decisions"] == []


def test_fresh_verified_aggregate_windows_validate_and_are_non_customer_data():
    result = validate_library(base_payload())
    assert [row["window_id"] for row in result["performance_windows"]] == ["window-one", "window-two"]
    assert all(row["freshness"] == "fresh" for row in result["performance_windows"])
    assert result["performance_windows"][0]["conversion_rate"] == "0.050000"
    assert "customer" not in str(result).lower()
    assert result["retirement_decisions"] == []
    assert "explicit_input_only" in result["decision_policy"]
    assert result["coverage"][0]["state"] == "fresh"


def test_missing_and_stale_coverage_are_explicit_not_zero():
    payload = base_payload()
    payload["performance_windows"] = []
    missing = validate_library(payload)["coverage"][0]
    assert missing["state"] == "missing"
    assert missing["window_count"] == 0
    assert missing["missing_is_not_zero"] is True

    payload = base_payload()
    payload["as_of_date"] = "2026-11-01"
    stale = validate_library(payload)["coverage"][0]
    assert stale["state"] == "stale"
    assert stale["fresh_window_count"] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["evidence_refs"][0].update(verification_status="unverified"),
        lambda p: p["evidence_refs"][0].update(sha256="not-a-digest"),
        lambda p: p["performance_windows"][0].update(brand="terafin"),
        lambda p: p["performance_windows"][0].update(evidence_ref_id="absent-evidence"),
        lambda p: p["performance_windows"][0].update(phone_number="+628123456789"),
    ],
)
def test_invalid_unverified_cross_brand_or_customer_fields_fail_closed(mutate):
    payload = base_payload()
    mutate(payload)
    with pytest.raises(InputError):
        validate_library(payload)


def test_identical_replay_is_idempotent_and_counted():
    payload = base_payload()
    payload["performance_windows"].append(copy.deepcopy(payload["performance_windows"][0]))
    result = validate_library(payload)
    assert len(result["performance_windows"]) == 2
    assert result["identical_replays_collapsed"] == 1


def test_conflicting_replay_is_rejected():
    payload = base_payload()
    replay = copy.deepcopy(payload["performance_windows"][0])
    replay["conversions"] = 6
    payload["performance_windows"].append(replay)
    with pytest.raises(InputError, match="conflicting window_id replay"):
        validate_library(payload)


def test_overlapping_distinct_windows_are_rejected():
    payload = base_payload()
    replay = copy.deepcopy(payload["performance_windows"][1])
    replay.update(window_id="window-three", window_start="2026-09-14", evidence_ref_id="evidence-two")
    payload["performance_windows"].append(replay)
    with pytest.raises(InputError, match="overlapping"):
        validate_library(payload)


def test_no_decision_is_inferred_from_low_performance():
    result = validate_library(base_payload())
    assert result["retirement_decisions"] == []
    assert result["hooks"][0]["lifecycle"] == "active"


def test_explicit_founder_approved_retirement_record_validates_without_mutating_hook():
    payload = base_payload()
    payload["retirement_decisions"] = [retirement_decision()]
    result = validate_library(payload)
    assert result["retirement_decisions"][0]["decision"] == "retire"
    assert result["retirement_decisions"][0]["qualifying_real_window_ids"] == ["window-one", "window-two"]
    assert result["hooks"][0]["lifecycle"] == "active"


def test_explicit_retain_requires_fresh_real_windows_above_threshold():
    payload = base_payload()
    decision = retirement_decision()
    decision["decision"] = "retain"
    payload["retirement_decisions"] = [decision]

    with pytest.raises(InputError, match="fresh performance windows"):
        validate_library({**payload, "performance_windows": []})

    for window in payload["performance_windows"]:
        window["engagements"] = 25
        window["conversions"] = 20
    result = validate_library(payload)
    assert result["retirement_decisions"][0]["decision"] == "retain"
    assert result["retirement_decisions"][0]["qualifying_real_window_ids"] == [
        "window-one",
        "window-two",
    ]


def test_retain_refuses_windows_below_the_approved_performance_threshold():
    payload = base_payload()
    decision = retirement_decision()
    decision["decision"] = "retain"
    payload["retirement_decisions"] = [decision]
    with pytest.raises(InputError, match="qualifying real windows"):
        validate_library(payload)


def test_retain_refuses_windows_exactly_at_the_approved_performance_threshold():
    payload = base_payload()
    decision = retirement_decision()
    decision["decision"] = "retain"
    payload["retirement_decisions"] = [decision]
    for window in payload["performance_windows"]:
        window["engagements"] = 20
        window["conversions"] = 10
    with pytest.raises(InputError, match="qualifying real windows"):
        validate_library(payload)


def test_retirement_accepts_exact_threshold_and_rejects_above_threshold():
    payload = base_payload()
    for window in payload["performance_windows"]:
        window["engagements"] = 20
        window["conversions"] = 10
    payload["retirement_decisions"] = [retirement_decision()]
    result = validate_library(payload)
    assert result["retirement_decisions"][0]["decision"] == "retire"

    for window in payload["performance_windows"]:
        window["conversions"] = 11
    with pytest.raises(InputError, match="qualifying real windows"):
        validate_library(payload)


@pytest.mark.parametrize(
    "evidence_ref_id",
    ["approval", "threshold-approval"],
)
def test_decision_approval_evidence_cannot_predate_hook_creation(evidence_ref_id):
    payload = base_payload()
    payload["hooks"][0]["created_on"] = "2026-09-08"
    next(item for item in payload["evidence_refs"] if item["evidence_ref_id"] == evidence_ref_id)["observed_on"] = (
        "2026-09-07"
    )
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="predates the hook version"):
        validate_library(payload)


@pytest.mark.parametrize("evidence_ref_id", ["approval", "threshold-approval"])
def test_decision_approval_evidence_cannot_postdate_decision(evidence_ref_id):
    payload = base_payload()
    item = next(item for item in payload["evidence_refs"] if item["evidence_ref_id"] == evidence_ref_id)
    item["observed_on"] = "2026-09-26"
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="postdates the decision"):
        validate_library(payload)


def test_decision_cannot_use_windows_or_evidence_dated_after_the_decision():
    payload = base_payload()
    decision = retirement_decision()
    decision["decided_on"] = "2026-09-20"
    payload["evidence_refs"][2]["observed_on"] = "2026-09-15"
    payload["evidence_refs"][3]["observed_on"] = "2026-09-15"
    payload["retirement_decisions"] = [decision]
    with pytest.raises(InputError, match="fresh performance windows"):
        validate_library(payload)


def test_performance_window_cannot_predate_hook_version_creation():
    payload = base_payload()
    payload["hooks"][0]["created_on"] = "2026-09-10"
    with pytest.raises(InputError, match="predates the hook version"):
        validate_library(payload)


def test_missing_windows_fail_closed_for_retirement():
    payload = base_payload()
    payload["performance_windows"] = []
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="fresh performance windows"):
        validate_library(payload)


def test_stale_windows_fail_closed_for_retirement():
    payload = base_payload()
    payload["as_of_date"] = "2026-11-01"
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="fresh performance windows"):
        validate_library(payload)


def test_synthetic_samples_never_qualify_as_real_retirement_outcomes():
    payload = base_payload()
    for window in payload["performance_windows"]:
        window["provenance"] = "synthetic"
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="qualifying real windows"):
        validate_library(payload)
    result = validate_library({**payload, "retirement_decisions": []})
    assert {row["outcome_classification"] for row in result["performance_windows"]} == {
        "synthetic_sample_not_real_outcome"
    }


def test_cross_brand_approval_evidence_fails_closed():
    payload = base_payload()
    payload["evidence_refs"][2]["brand"] = "terafin"
    payload["retirement_decisions"] = [retirement_decision()]
    with pytest.raises(InputError, match="same-brand founder approval"):
        validate_library(payload)


def test_approval_threshold_and_performance_evidence_are_mandatory():
    payload = base_payload()
    payload["retirement_decisions"] = [retirement_decision()]
    payload["evidence_refs"][3]["evidence_kind"] = "performance_source"
    with pytest.raises(InputError, match="threshold approval evidence"):
        validate_library(payload)


def test_unsupported_schema_and_funnel_values_refuse():
    payload = base_payload()
    payload["schema_version"] = 2
    with pytest.raises(InputError):
        validate_library(payload)
    payload = base_payload()
    payload["performance_windows"][0]["conversions"] = 11
    with pytest.raises(InputError, match="funnel ordering"):
        validate_library(payload)


def test_cli_json_parser_refuses_duplicate_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate object key"):
        _load_input(path)


def test_cli_json_parser_normalizes_excessive_nesting(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 65 + "0" + "]" * 65, encoding="utf-8")
    with pytest.raises(InputError, match="64-level JSON nesting"):
        _load_input(path)
