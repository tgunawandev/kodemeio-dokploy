from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "opportunity_research.v1.schema.json"
EXAMPLE_PATH = ROOT / "examples" / "opportunity_research.synthetic.v1.json"
SCRIPT = ROOT / "scripts" / "opportunity_research.py"

sys.path.insert(0, str(ROOT / "scripts"))
from opportunity_research import InputError, claim_sha256, evaluate, load_document  # noqa: E402


def payload() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_strict_contract_accepts_synthetic_claim_with_explicit_unverified_review() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    document = payload()
    Draft202012Validator(schema).validate(document)

    report = evaluate(document)
    assert report == {
        "status": "candidate-unverified",
        "issues": [],
        "source_assertions_verified": False,
        "manual_review_required": True,
        "owner_review_authenticated": False,
        "external_access": False,
    }


def test_claim_hash_must_bind_exact_captured_text() -> None:
    document = payload()
    document["opportunities"][0]["claim"]["sha256"] = "0" * 64
    assert "opportunities[0].claim:hash_mismatch" in evaluate(document)["issues"]


def test_captured_evidence_hash_must_bind_exact_claim_text() -> None:
    document = payload()
    document["evidence"][0]["captured_claim"] += " changed"
    assert "evidence[0].captured_claim:hash_mismatch" in evaluate(document)["issues"]


def test_every_claim_must_bind_existing_evidence() -> None:
    document = payload()
    document["opportunities"][0]["evidence_refs"] = ["ev-missing"]
    issues = evaluate(document)["issues"]
    assert "opportunities[0].evidence_refs:unknown_reference" in issues


def test_owner_review_requires_a_bounded_opaque_owner_reference() -> None:
    document = payload()
    document["opportunities"][0]["owner_review"]["owner_ref"] = ""
    assert evaluate(document)["status"] == "blocked"
    assert any("owner_review.owner_ref" in issue for issue in evaluate(document)["issues"])


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_evidence_reference_is_refused_even_when_identical(conflict: bool) -> None:
    document = payload()
    duplicate = copy.deepcopy(document["evidence"][0])
    if conflict:
        duplicate["captured_claim"] += " Conflicting synthetic addition."
        duplicate["captured_claim_sha256"] = claim_sha256(duplicate["captured_claim"])
    document["evidence"].append(duplicate)
    expected = "evidence:conflicting_reference" if conflict else "evidence:duplicate_reference"
    assert expected in evaluate(document)["issues"]


def test_duplicate_capture_is_refused_even_under_a_second_opaque_reference() -> None:
    document = payload()
    duplicate = copy.deepcopy(document["evidence"][0])
    duplicate["evidence_ref"] = "ev-synth-02"
    document["evidence"].append(duplicate)
    assert "evidence:duplicate_capture" in evaluate(document)["issues"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("captured_claim", "Synthetic source includes person@example.test in a note."),
        ("captured_claim", "Synthetic source includes bearer abcdefghijklmnop credential."),
        ("captured_claim", "Synthetic source points to https://example.test/path."),
        ("source_domain", "https://example.test/path"),
    ],
)
def test_pii_credentials_and_url_values_are_refused(field: str, value: str) -> None:
    document = payload()
    evidence = document["evidence"][0]
    evidence[field] = value
    if field == "captured_claim":
        evidence["captured_claim_sha256"] = claim_sha256(value)
    assert evaluate(document)["status"] == "blocked"


def test_owner_review_never_verifies_source_assertions() -> None:
    document = payload()
    review = document["opportunities"][0]["owner_review"]
    review.update(status="reviewed_unverified", reviewed_on="2026-09-28")
    document["opportunities"][0]["status"] = "reviewed_unverified"
    report = evaluate(document)
    assert report["status"] == "candidate-unverified"
    assert report["source_assertions_verified"] is False
    assert report["manual_review_required"] is True
    assert report["owner_review_authenticated"] is False


@pytest.mark.parametrize(
    "review_status,reviewed_on,expected_issue",
    [
        ("pending", "2026-09-28", "opportunities[0].owner_review:pending_has_review_date"),
        ("reviewed_unverified", None, "opportunities[0].owner_review:review_date_required"),
        ("rejected", None, "opportunities[0].owner_review:review_date_required"),
        ("reviewed_unverified", "2026-09-29", "opportunities[0].owner_review:after_as_of_date"),
    ],
)
def test_owner_review_date_invariants(review_status, reviewed_on, expected_issue):
    document = payload()
    review = document["opportunities"][0]["owner_review"]
    review.update(status=review_status, reviewed_on=reviewed_on)
    document["opportunities"][0]["status"] = {
        "pending": "unreviewed",
        "reviewed_unverified": "reviewed_unverified",
        "rejected": "rejected",
    }[review_status]
    assert expected_issue in evaluate(document)["issues"]


def test_candidate_state_must_match_manual_review_state() -> None:
    document = payload()
    review = document["opportunities"][0]["owner_review"]
    review.update(status="reviewed_unverified", reviewed_on="2026-09-28")
    assert "opportunities[0].status:owner_review_mismatch" in evaluate(document)["issues"]


def test_future_capture_and_unknown_keys_are_refused() -> None:
    document = payload()
    document["evidence"][0]["captured_on"] = "2026-09-29"
    report = evaluate(document)
    assert report["status"] == "blocked"
    assert any("captured_on" in issue for issue in report["issues"])


def test_unknown_keys_are_refused_without_echoing_values() -> None:
    document = payload()
    document["unrecognized"] = "synthetic-canary"
    report = evaluate(document)
    assert report["status"] == "blocked"
    assert "document:root:invalid_shape" in report["issues"]
    assert "synthetic-canary" not in json.dumps(report)


def test_duplicate_json_keys_and_oversized_inputs_are_refused(tmp_path: Path) -> None:
    duplicate_keys = tmp_path / "duplicate.json"
    duplicate_keys.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(InputError, match="duplicate_json_key"):
        load_document(duplicate_keys)

    too_large = tmp_path / "too-large.json"
    too_large.write_bytes(b" " * 262145)
    with pytest.raises(InputError, match="input_too_large"):
        load_document(too_large)


def test_deep_json_is_refused_but_brackets_in_strings_are_ordinary_text(
    tmp_path: Path,
) -> None:
    too_deep = tmp_path / "too-deep.json"
    too_deep.write_text("[" * 33 + "0" + "]" * 33, encoding="utf-8")
    with pytest.raises(InputError, match="input_too_deep"):
        load_document(too_deep)

    quoted = tmp_path / "quoted-brackets.json"
    quoted.write_text('{"text":"' + "[" * 64 + '"}', encoding="utf-8")
    assert load_document(quoted) == {"text": "[" * 64}

    escaped_quote = tmp_path / "escaped-quote.json"
    escaped_quote.write_text('{"text":"escaped \\" [ ] { }"}', encoding="utf-8")
    assert load_document(escaped_quote) == {"text": 'escaped " [ ] { }'}

    at_limit = tmp_path / "at-limit.json"
    at_limit.write_text("[" * 32 + "0" + "]" * 32, encoding="utf-8")
    expected = 0
    for _ in range(32):
        expected = [expected]
    assert load_document(at_limit) == expected


def test_cli_is_deterministic_and_never_marks_assertions_verified() -> None:
    command = [sys.executable, str(SCRIPT), "validate", str(EXAMPLE_PATH)]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["source_assertions_verified"] is False
    assert report["external_access"] is False
    assert report["manual_review_required"] is True
    assert report["owner_review_authenticated"] is False
