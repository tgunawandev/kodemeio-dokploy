from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privacy_inventory as inventory  # noqa: E402
from privacy_inventory import InputError, load, parse_record, validate  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/scripts/privacy_inventory.py"
LEGACY = ROOT / "ops/scripts/privacy_data_map.py"
SAMPLE = ROOT / "ops/examples/privacy_inventory.synthetic.v2.json"
RECORD_FILES = [
    ROOT / "ops/examples/privacy_deletion_evidence.synthetic-a.v1.json",
    ROOT / "ops/examples/privacy_deletion_evidence.synthetic-b.v1.json",
]
SCHEMA = ROOT / "ops/contracts/privacy_inventory.v2.schema.json"
AS_OF = "2026-09-28"
UNKNOWN_PRODUCT_ID = "prod_ffffffffffffffffffffffffffffffff"


def payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def records() -> list[inventory.DeletionRecord]:
    return [parse_record(load(path)) for path in RECORD_FILES]


def good(data: dict | None = None, **kwargs) -> dict:
    return validate(data or payload(), AS_OF, records(), allow_local_fake=True, **kwargs)


def run_cli(*args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *args], check=False, capture_output=True, text=True)


def record_args() -> list[str]:
    return [item for path in RECORD_FILES for item in ("--deletion-record", str(path))]


# --------------------------------------------------------------------------- schema and happy path


def test_schema_and_synthetic_inventory_are_valid_but_never_verified() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload()))
    report = good()
    assert report["status"] == inventory.OK_STATUS
    assert report["deletion_records_matched"] == 2
    assert report["verified"] is False
    assert report["legal_reviewed"] is False
    assert report["deletion_verified"] is False


def test_schema_rejects_basic_evidence_state_mismatches() -> None:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    cases = [
        lambda p: p["products"][0].update(product_id="jane-doe"),
        lambda p: p["processors"][0].update(evidence_refs=[]),
        lambda p: p["products"][0].update(consent_evidence_refs=[]),
        lambda p: p["products"][0].update(deletion_evidence_refs=[]),
        lambda p: p["products"][0].update(data_flows=[]),
        lambda p: p["products"][0].update(stores=[]),
        lambda p: p["products"][0]["stores"][3].update(retention_days=None),
        lambda p: p["products"][0]["stores"][0].update(retention_days=30),
    ]
    for mutate in cases:
        data = payload()
        mutate(data)
        assert list(validator.iter_errors(data))


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (("processors", 0, "processor_id"), "proc"),
        (("evidence_refs", 0, "evidence_ref_id"), "ev"),
        (("purpose_registry", 0, "purpose_id"), "purp"),
        (("products", 0, "data_flows", 0, "flow_id"), "flow"),
        (("products", 0, "stores", 0, "store_id"), "store"),
    ],
)
@pytest.mark.parametrize("label", ["john-doe", "first-party-service", "ev-consent"])
def test_every_identifier_must_be_an_opaque_typed_token(path: tuple, kind: str, label: str) -> None:
    data = payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = label
    with pytest.raises(InputError, match=f"opaque generated {kind}_ identifier") as excinfo:
        good(data)
    assert label not in str(excinfo.value)
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
    assert list(validator.iter_errors(data))


def test_name_like_or_newline_product_ids_are_rejected() -> None:
    for value in ("jane-doe", payload()["products"][0]["product_id"] + "\n"):
        data = payload()
        data["products"][0]["product_id"] = value
        with pytest.raises(InputError, match="opaque generated product identifier"):
            good(data)


# --------------------------------------------------------------------------- unresolved states and exit code


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
    report = good(data)
    assert report["status"] == "incomplete"
    assert report["unresolved_count"] >= 6
    assert "products[0]:inventory_status:unknown" in report["unresolved"]


def test_none_declared_processor_inventory_is_distinct_and_unverified() -> None:
    data = payload()
    data.update(processor_inventory_status="none_declared", processors=[])
    for product in data["products"]:
        product.update(inventory_status="unknown", data_flows=[], stores=[])
    data["evidence_refs"] = [item for item in data["evidence_refs"] if item["product_id"] is not None]
    report = good(data)
    assert "processor_inventory_status:none_declared_unverified" in report["unresolved"]


def test_unknown_hosting_region_and_other_store_kind_are_unresolved() -> None:
    data = payload()
    data["processors"][0]["hosting_region"] = "unknown"
    data["products"][1]["stores"][0]["store_kind"] = "other"
    report = validate(data, AS_OF)
    assert "processors[0]:hosting_region:unknown" in report["unresolved"]
    assert "products[1].stores[0]:store_kind:other" in report["unresolved"]


def test_retention_rules_must_cover_every_held_category() -> None:
    data = payload()
    data["products"][0]["retention_rules"] = [
        rule for rule in data["products"][0]["retention_rules"] if rule["data_category"] != "support_message"
    ]
    data["products"][0]["retention_rules"][0]["retention_days"] = None
    report = good(data)
    first = data["products"][0]["retention_rules"][0]["data_category"]
    assert "products[0]:retention_rule:support_message:missing" in report["unresolved"]
    assert f"products[0]:retention_rule:{first}:period_unresolved" in report["unresolved"]
    data["products"][0]["retention_rules"].append(copy.deepcopy(data["products"][0]["retention_rules"][1]))
    with pytest.raises(InputError, match="duplicate categories"):
        good(data)


def test_failed_deletion_status_makes_cli_exit_non_zero(tmp_path: Path) -> None:
    data = payload()
    data["products"][0]["deletion_test_status"] = "failed"
    document = tmp_path / "failed.json"
    document.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli("--as-of", AS_OF, "--allow-local-fake-evidence", *record_args(), str(document))
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["status"] == "incomplete"
    assert "products[0]:deletion_test_status:failed" in report["unresolved"]


def test_passed_status_without_a_matching_record_is_missing_and_non_zero() -> None:
    report = validate(payload(), AS_OF, allow_local_fake=True)
    assert report["status"] == "incomplete"
    assert "products[0]:deletion_record:missing" in report["unresolved"]
    result = run_cli("--as-of", AS_OF, str(SAMPLE))
    assert result.returncode == 1
    assert "products[1]:deletion_record:missing" in json.loads(result.stdout)["unresolved"]


def test_not_run_deletion_test_is_unresolved() -> None:
    data = payload()
    data["products"][1].update(deletion_test_status="not_run", deletion_tested_on=None, deletion_evidence_refs=[])
    report = good(data)
    assert "products[1]:deletion_test_status:not_run" in report["unresolved"]


# --------------------------------------------------------------------------- deletion-evidence binding


def test_local_fake_records_are_refused_unless_explicitly_allowed() -> None:
    report = validate(payload(), AS_OF, records())
    assert "products[0]:deletion_record:local_fake_environment" in report["unresolved"]
    assert good()["status"] == inventory.OK_STATUS


def test_stale_record_is_unresolved() -> None:
    data = payload()
    data["as_of_date"] = "2027-01-01"
    report = validate(data, "2027-01-01", records(), allow_local_fake=True)
    assert "products[0]:deletion_record:stale" in report["unresolved"]


def test_record_must_cover_the_current_store_map() -> None:
    data = payload()
    data["products"][0]["stores"][0]["data_categories"].append("other")
    report = good(data)
    assert "products[0]:deletion_record:store_map_mismatch" in report["unresolved"]


def test_record_cannot_be_reused_across_products_or_dates() -> None:
    data = payload()
    delete_a = data["products"][0]["deletion_evidence_refs"][0]
    delete_b = data["products"][1]["deletion_evidence_refs"][0]
    sha_a = next(item["sha256"] for item in data["evidence_refs"] if item["evidence_ref_id"] == delete_a)
    for item in data["evidence_refs"]:
        if item["evidence_ref_id"] == delete_b:
            item["sha256"] = sha_a
    report = good(data)
    assert "products[1]:deletion_record:cross_product_scope" in report["unresolved"]

    data = payload()
    for item in data["evidence_refs"]:
        if item["evidence_ref_id"] == delete_a:
            item["observed_on"] = "2026-09-28"
    data["products"][0]["deletion_tested_on"] = "2026-09-27"
    report = good(data)
    assert "products[0]:deletion_record:date_mismatch" in report["unresolved"]


def test_tampered_record_no_longer_matches_inventory_digest() -> None:
    raw = load(RECORD_FILES[0])
    raw["body"]["stores"][0]["residue_count"] = 0
    raw["body"]["valid_until"] = "2027-01-01"
    tampered = [parse_record(raw), parse_record(load(RECORD_FILES[1]))]
    report = validate(payload(), AS_OF, tampered, allow_local_fake=True)
    assert "products[0]:deletion_record:missing" in report["unresolved"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["body"].update(status="failed"),
        lambda r: r["body"]["stores"][0].update(outcome="residue"),
        lambda r: r["body"].update(valid_until="2026-09-01"),
        lambda r: r["body"].update(kind="other"),
        lambda r: r["body"]["stores"].append(copy.deepcopy(r["body"]["stores"][0])),
        lambda r: r.update(signature={"alg": "md5", "key_id": "key_" + "0" * 32, "value": "0" * 64}),
        lambda r: r["body"].update(extra=True),
    ],
)
def test_malformed_or_self_contradictory_records_are_invalid(mutate) -> None:
    raw = load(RECORD_FILES[0])
    mutate(raw)
    with pytest.raises(InputError):
        parse_record(raw)


def test_hmac_signature_is_required_when_a_key_is_supplied(tmp_path: Path) -> None:
    key = b"k" * 32
    report = good(hmac_key=key)
    assert "products[0]:deletion_record:signature_invalid" in report["unresolved"]
    key_file = tmp_path / "key"
    key_file.write_bytes(b"short")
    result = run_cli("--as-of", AS_OF, "--hmac-key-file", str(key_file), str(SAMPLE))
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "signing_key_must_be_32_to_4096_bytes"


# --------------------------------------------------------------------------- structural refusals


def test_linked_evidence_count_includes_processor_product_and_flow_claims() -> None:
    data = payload()
    report = good(data)
    expected = sum(len(processor["evidence_refs"]) for processor in data["processors"])
    expected += sum(
        len(product[name])
        for product in data["products"]
        for name in ("consent_evidence_refs", "retention_evidence_refs", "deletion_evidence_refs", "pia_evidence_refs")
    )
    expected += sum(len(flow["evidence_refs"]) for product in data["products"] for flow in product["data_flows"])
    assert report["linked_evidence_reference_count"] == expected


def test_identified_processor_requires_source_reference() -> None:
    data = payload()
    data["processors"][0]["evidence_refs"] = []
    with pytest.raises(InputError, match="non-empty"):
        good(data)


MISSING_PROC = "proc_" + "9" * 32
MISSING_EV = "ev_" + "9" * 32
MISSING_PURP = "purp_" + "9" * 32


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["products"][0]["data_flows"][0].update(processor_id=MISSING_PROC),
        lambda p: p["products"][0]["data_flows"][0].update(evidence_refs=[MISSING_EV]),
        lambda p: p["products"][0]["data_flows"][0].update(purpose_id=MISSING_PURP),
        lambda p: p["products"][0]["data_flows"][0].update(store_id=p["products"][1]["stores"][0]["store_id"]),
        lambda p: p["products"][0]["stores"][0].update(processor_id=MISSING_PROC),
        lambda p: p["products"][1]["stores"].append(copy.deepcopy(p["products"][0]["stores"][0])),
        lambda p: p["products"][0].update(consent_status="documented", consent_evidence_refs=[]),
        lambda p: p["products"][0].update(deletion_tested_on="2026-09-29"),
        lambda p: p["evidence_refs"][0].update(sha256="bad"),
        lambda p: p["products"][0]["data_flows"][0].update(data_categories=["contact", "contact"]),
        lambda p: p["products"][0]["stores"][0].update(erasure_mode="retention_bound", retention_days=None),
        lambda p: p["products"][0]["stores"][0].update(retention_days=7),
        lambda p: p.update(schema_version=1),
    ],
)
def test_broken_references_or_inconsistent_evidence_fail_closed(mutate) -> None:
    data = payload()
    mutate(data)
    with pytest.raises(InputError):
        good(data)


def test_product_and_flow_identifiers_must_be_unique() -> None:
    data = payload()
    data["products"].append(copy.deepcopy(data["products"][0]))
    with pytest.raises(InputError, match="product_id"):
        good(data)


def test_deletion_evidence_cannot_predate_test() -> None:
    data = payload()
    delete_ref = data["products"][0]["deletion_evidence_refs"][0]
    for item in data["evidence_refs"]:
        if item["evidence_ref_id"] == delete_ref:
            item["observed_on"] = "2026-09-23"
    with pytest.raises(InputError, match="predates"):
        good(data)


def test_purpose_and_evidence_scope_reject_cross_product_and_dangling_references() -> None:
    data = payload()
    other_purpose = data["purpose_registry"][2]["purpose_id"]
    data["products"][0]["data_flows"][0]["purpose_id"] = other_purpose
    with pytest.raises(InputError, match="cross-product purpose"):
        good(data)
    data = payload()
    data["purpose_registry"].append(
        {"product_id": UNKNOWN_PRODUCT_ID, "purpose_id": "purp_" + "8" * 32, "purpose_kind": "other"}
    )
    with pytest.raises(InputError, match="unknown product"):
        good(data)
    data = payload()
    data["products"][0]["data_flows"][0]["evidence_refs"] = [data["processors"][0]["evidence_refs"][0]]
    with pytest.raises(InputError, match="outside this product scope"):
        good(data)
    data = payload()
    data["processors"][0]["evidence_refs"] = [data["products"][0]["consent_evidence_refs"][0]]
    with pytest.raises(InputError, match="package-scoped evidence"):
        good(data)


def test_bad_shapes_never_crash_or_echo_untrusted_values() -> None:
    data = payload()
    data["products"][0]["consent_status"] = {"private-canary": "bad"}
    with pytest.raises(InputError) as excinfo:
        good(data)
    assert "private-canary" not in str(excinfo.value)
    with pytest.raises(InputError):
        good({"private-canary": "bad"})


def test_loader_rejects_duplicate_keys_oversize_invalid_utf8_and_deep_json(tmp_path: Path) -> None:
    cases = {
        "duplicate_json_key": b'{"schema_version":2,"schema_version":2}',
        "input_too_large": b" " * 1_000_001,
        "input_unreadable_or_invalid_json": b"\xff",
        "input_too_deep": b"[" * 33 + b"0" + b"]" * 33,
    }
    for error, content in cases.items():
        document = tmp_path / f"{error}.json"
        document.write_bytes(content)
        with pytest.raises(InputError, match=error):
            load(document)
    quoted = tmp_path / "quoted.json"
    quoted.write_text('{"note":"escaped \\" [ ] { } ' + "[" * 64 + '"}', encoding="utf-8")
    assert load(quoted) == {"note": 'escaped " [ ] { } ' + "[" * 64}


def test_huge_json_integer_cli_refuses_with_invalid_exit_code(tmp_path: Path) -> None:
    document = tmp_path / "huge-integer.json"
    document.write_text('{"schema_version":' + "9" * 5000 + "}", encoding="utf-8")
    result = run_cli("--as-of", AS_OF, str(document))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["error"] == "json_integer_out_of_range"


def test_cli_complete_package_exits_zero_and_emits_only_unverified_status() -> None:
    result = run_cli("--as-of", AS_OF, "--allow-local-fake-evidence", *record_args(), str(SAMPLE))
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout
    assert report["status"] == inventory.OK_STATUS
    assert report["verified"] is False and report["legal_reviewed"] is False and report["deletion_verified"] is False
    assert "compliant" not in result.stdout.lower()


def test_deprecated_data_map_entry_warns_and_delegates() -> None:
    result = run_cli("--as-of", AS_OF, "--allow-local-fake-evidence", *record_args(), str(SAMPLE), script=LEGACY)
    assert result.returncode == 0
    assert "deprecated" in result.stderr
    assert json.loads(result.stdout)["status"] == inventory.OK_STATUS
    legacy_style = run_cli(str(SAMPLE), script=LEGACY)
    assert legacy_style.returncode != 0
    assert "deprecated" in legacy_style.stderr


# ---------------------------------------------------------------- review fixwave: self-declared record claims


EVIDENCE_SCHEMA = ROOT / "ops/contracts/privacy_deletion_evidence.v1.schema.json"


def test_evidence_schema_accepts_emitted_records_and_rejects_contradictions() -> None:
    schema = json.loads(EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for path in RECORD_FILES:
        assert not list(validator.iter_errors(load(path)))
    erase_store = next(i for i, s in enumerate(load(RECORD_FILES[0])["body"]["stores"]) if s["erasure_mode"] == "erase")
    for mutate in (
        lambda r: r["body"]["stores"][erase_store].update(outcome="tombstoned"),
        lambda r: r["body"]["stores"][erase_store].update(residue_count=5),
    ):
        raw = load(RECORD_FILES[0])
        mutate(raw)
        assert list(validator.iter_errors(raw))
        with pytest.raises(InputError):
            parse_record(raw)


def test_record_validity_window_is_bounded() -> None:
    raw = load(RECORD_FILES[0])
    raw["body"]["valid_until"] = "2126-01-01"
    with pytest.raises(InputError, match="exceeds 366 days"):
        parse_record(raw)


def _rebind(data: dict, index: int, raw: dict) -> list:
    ref = data["products"][index]["deletion_evidence_refs"][0]
    for item in data["evidence_refs"]:
        if item["evidence_ref_id"] == ref:
            item["sha256"] = inventory.record_body_digest(raw["body"])
    return [parse_record(raw), parse_record(load(RECORD_FILES[1]))]


def test_record_store_kind_and_mode_must_match_the_inventory_map() -> None:
    data = payload()
    raw = load(RECORD_FILES[0])
    target = next(s for s in raw["body"]["stores"] if s["store_kind"] == "supabase_row")
    target["store_kind"] = "chatwoot_contact"
    report = validate(data, AS_OF, _rebind(data, 0, raw), allow_local_fake=True)
    assert "products[0]:deletion_record:store_map_mismatch" in report["unresolved"]


def test_non_fake_record_requires_a_verified_signature() -> None:
    data = payload()
    raw = load(RECORD_FILES[0])
    raw["body"]["environment"] = "production_drill"
    records_ = _rebind(data, 0, raw)
    report = validate(data, AS_OF, records_)
    assert report["status"] == "incomplete"
    assert "products[0]:deletion_record:unsigned_or_unverified" in report["unresolved"]
    key = b"q" * 32
    raw["signature"] = {
        "alg": "hmac-sha256",
        "key_id": "key_" + "b" * 32,
        "value": inventory.sign_body(raw["body"], key),
    }
    records_ = [parse_record(raw), parse_record(load(RECORD_FILES[1]))]
    report = validate(data, AS_OF, records_, allow_local_fake=True, hmac_key=key)
    assert "products[0]:deletion_record:unsigned_or_unverified" not in report["unresolved"]
    assert not [item for item in report["unresolved"] if item.startswith("products[0]")]
