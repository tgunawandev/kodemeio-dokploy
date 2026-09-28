from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teracorp_g6_monthly_evidence as g6  # noqa: E402
from teracorp_g6_monthly_evidence import MAX_JSON_DEPTH, InputError, load_document  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "teracorp_g6_monthly_evidence.v1.schema.json"
SAMPLE = ROOT / "examples" / "teracorp_g6_monthly_evidence.synthetic.v1.json"
# The first instant after the 2026-09 package month has closed.
AS_OF = "2026-10-01T00:00:00Z"


def evaluate(payload, as_of):
    """Evaluate the checked-in synthetic fixture in explicit synthetic-fixture mode."""
    return g6.evaluate(payload, as_of, allow_synthetic=True)


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
    decision["evidence_refs"].append("ev-11111111111111111111111111111111")
    payload["incident_inventory"]["items"].append(
        {
            "incident_id": "incident-synthetic-1",
            "handling_status": "handled",
            "evidence_refs": ["ev-11111111111111111111111111111111"],
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
        "manual_counsel_review_required": True,
        "counsel_identity_authenticated": False,
        "evidence_authenticated": False,
        "inventory_exhaustiveness_verified": False,
        "as_of_authenticated": False,
        "synthetic_fixture_mode": True,
        "inventory_counts": {"decisions": 8, "change_items": 2, "incident_items": 0},
    }


@pytest.mark.parametrize("subject_type", ["product", "processor"])
def test_change_evidence_must_be_linked_to_scoped_counsel_decision(subject_type: str) -> None:
    payload = valid_payload()
    item = next(row for row in payload["change_inventory"]["items"] if row["subject_type"] == subject_type)
    item["evidence_refs"] = ["ev-22222222222222222222222222222222"]
    assert_incomplete(
        payload,
        f"change_inventory.items[{payload['change_inventory']['items'].index(item)}]:evidence_unlinked_from_decision",
    )


def test_incident_evidence_must_be_linked_to_scoped_counsel_decision() -> None:
    payload = valid_payload()
    add_incident_decision(payload)
    payload["incident_inventory"]["items"][0]["evidence_refs"] = ["ev-22222222222222222222222222222222"]
    assert_incomplete(payload, "incident_inventory.items[0]:evidence_unlinked_from_decision")


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
    payload["decisions"][0]["issued_at"] = "2026-10-01T00:00:01Z"
    payload["decisions"][0]["valid_from"] = "2026-10-01T00:00:01Z"
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


@pytest.mark.parametrize(
    "evidence_ref",
    [
        "customer:jane.doe/order-123",
        "jane.doe",
        "ev.synthetic.jane.doe",
        "ev-0123456789abcdef0123456789abcdef\n",
        "qual-0123456789abcdef0123456789abcdef\n",
    ],
)
def test_evidence_references_reject_human_identifiers(evidence_ref: str) -> None:
    payload = valid_payload()
    payload["decisions"][0]["evidence_refs"] = [evidence_ref]
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
    assert_incomplete(payload, "document.decisions.0.evidence_refs.0:invalid_shape")


def test_evidence_reference_accepts_opaque_machine_token() -> None:
    payload = valid_payload()
    payload["decisions"][0]["evidence_refs"] = ["ev-0123456789abcdef0123456789abcdef"]
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
    assert evaluate(payload, AS_OF)["status"] == "complete-for-counsel-review-unverified"


@pytest.mark.parametrize("as_of", ["2026-09-28T00:00:00+07:00", "2026-09-27T19:00:00-05:00"])
def test_non_utc_as_of_is_rejected(as_of: str) -> None:
    assert_incomplete(valid_payload(), "as_of:utc_timestamp_required", as_of)
    with pytest.raises(g6.argparse.ArgumentTypeError, match="as_of_requires_utc_timestamp"):
        g6.parse_as_of(as_of)


def test_utc_offset_as_of_is_accepted() -> None:
    assert g6.parse_as_of("2026-09-28T00:00:00+00:00") == "2026-09-28T00:00:00+00:00"


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


def test_excessive_json_container_nesting_is_rejected_before_parse(tmp_path: Path) -> None:
    path = tmp_path / "deeply-nested.json"
    nesting = MAX_JSON_DEPTH + 1
    path.write_text("[" * nesting + "0" + "]" * nesting, encoding="utf-8")
    with pytest.raises(InputError, match="json_nesting_too_deep"):
        load_document(path)


def test_bracket_heavy_json_string_does_not_count_toward_nesting_limit(tmp_path: Path) -> None:
    path = tmp_path / "brackets-in-string.json"
    bracket_text = "[]{}" * (MAX_JSON_DEPTH + 1)
    path.write_text(json.dumps({"marker": bracket_text}), encoding="utf-8")
    assert load_document(path) == {"marker": bracket_text}


def test_parser_recursion_error_is_normalized_to_sanitized_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "recursion-error.json"
    path.write_text("{}", encoding="utf-8")

    def raise_recursion_error(*args: object, **kwargs: object) -> None:
        raise RecursionError("untrusted parser detail")

    monkeypatch.setattr(g6.json, "loads", raise_recursion_error)
    with pytest.raises(InputError, match="input_unreadable_or_invalid_json") as error:
        load_document(path)
    assert "untrusted parser detail" not in str(error.value)


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
        [sys.executable, str(script), "--synthetic-fixture", "--as-of", AS_OF, str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["synthetic_fixture_mode"] is True
    assert report["status"] == "complete-for-counsel-review-unverified"
    assert report["verified"] is False and report["legal_reviewed"] is False
    assert "compliant" not in result.stdout.lower()


def test_huge_json_integer_cli_refuses_without_traceback(tmp_path: Path) -> None:
    document = tmp_path / "huge-integer.json"
    document.write_text('{"schema_version":' + "9" * 5000 + "}", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "teracorp_g6_monthly_evidence.py"
    result = subprocess.run(
        [sys.executable, str(script), "--as-of", AS_OF, str(document)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["issues"] == ["input:json_integer_out_of_range"]


def test_unresolved_processor_inventory_is_not_treated_as_no_processors() -> None:
    payload = valid_payload()
    payload["change_inventory"]["processor_inventory_status"] = "unknown"
    assert_incomplete(payload, "change_inventory:processor_inventory_unknown")


def test_incident_decision_must_cover_exact_incident() -> None:
    payload = valid_payload()
    add_incident_decision(payload)
    payload["incident_inventory"]["items"][0]["decision_id"] = "d-incident"
    assert_incomplete(payload, "incident_inventory.items[0]:decision_uncovered")


def _replace_synthetic_refs(payload: dict) -> dict:
    """Swap every synthetic ref for a deterministic opaque production-shaped token."""
    text = json.dumps(payload)
    tokens: dict[str, str] = {}
    for match in sorted(set(__import__("re").findall(r'"((?:ev|qual)\.synthetic\.[a-z0-9.-]+)"', text))):
        prefix = "qual" if match.startswith("qual") else "ev"
        tokens[match] = f"{prefix}-{len(tokens):032x}"
    for synthetic, opaque in tokens.items():
        text = text.replace(f'"{synthetic}"', f'"{opaque}"')
    return json.loads(text)


def test_production_mode_refuses_synthetic_refs_copied_from_the_example() -> None:
    report = g6.evaluate(valid_payload(), AS_OF)
    assert report["status"] == "incomplete"
    assert "document:synthetic_evidence_ref_not_allowed" in report["issues"]
    assert report["synthetic_fixture_mode"] is False


def test_production_mode_accepts_opaque_refs() -> None:
    report = g6.evaluate(_replace_synthetic_refs(valid_payload()), AS_OF)
    assert report["issues"] == []
    assert report["status"] == "complete-for-counsel-review-unverified"
    assert report["synthetic_fixture_mode"] is False


def test_single_synthetic_ref_in_otherwise_real_package_is_refused() -> None:
    payload = _replace_synthetic_refs(valid_payload())
    payload["decisions"][0]["counsel"]["qualification_refs"] = ["qual.synthetic.a"]
    assert "document:synthetic_evidence_ref_not_allowed" in g6.evaluate(payload, AS_OF)["issues"]


def test_cli_without_synthetic_flag_refuses_the_synthetic_example() -> None:
    script = Path(__file__).resolve().parents[1] / "teracorp_g6_monthly_evidence.py"
    result = subprocess.run(
        [sys.executable, str(script), "--as-of", AS_OF, str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "document:synthetic_evidence_ref_not_allowed" in json.loads(result.stdout)["issues"]


@pytest.mark.parametrize("as_of", ["2026-09-01T00:00:00Z", "2026-09-28T00:00:00Z", "2026-09-30T23:59:59Z"])
def test_as_of_before_month_end_blocks_completeness(as_of: str) -> None:
    payload = valid_payload()
    for decision in payload["decisions"]:
        decision["issued_at"] = decision["valid_from"] = "2026-09-01T00:00:00Z"
    assert_incomplete(payload, "as_of:before_month_end", as_of)


def test_as_of_at_first_instant_after_month_end_is_accepted() -> None:
    assert evaluate(valid_payload(), "2026-10-01T00:00:00Z")["status"] == "complete-for-counsel-review-unverified"


def test_december_month_end_rolls_into_next_year() -> None:
    payload = valid_payload()
    text = json.dumps(payload).replace("2026-09", "2026-12").replace("2026-10-20", "2027-01-20")
    payload = json.loads(text)
    assert_incomplete(payload, "as_of:before_month_end", "2026-12-31T23:59:59Z")
    assert evaluate(payload, "2027-01-01T00:00:00Z")["status"] == "complete-for-counsel-review-unverified"
