from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mor_candidate", ROOT / "candidate.py")
assert SPEC is not None and SPEC.loader is not None
MOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOR)


def event(event_id: str, kind: str, time: str, *, amount: int = 1000, currency: str = "USD") -> dict:
    return {
        "contract_version": "merchant_event.v1",
        "event_id": event_id,
        "provider_event_ref": f"evt:{event_id}",
        "event_type": kind,
        "occurred_at": f"2026-09-28T{time}:00Z",
        "order_ref": "order:synthetic-1",
        "payment_ref": "payment:synthetic-1",
        "amount_minor": amount,
        "currency": currency,
        "verification": {"status": "unverified", "adapter": "none"},
    }


def order(*, amount: int = 1000, currency: str = "USD") -> dict:
    return {"order_ref": "order:synthetic-1", "amount_minor": amount, "currency": currency}


def test_contract_is_strict_and_requires_unverified_state() -> None:
    schema = json.loads((ROOT / "merchant_event.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    format_checker = FormatChecker()

    @format_checker.checks("date-time")
    def canonical_utc_timestamp(value: object) -> bool:
        if not isinstance(value, str) or not value.endswith("Z"):
            return False
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            return False
        return parsed.utcoffset() == timedelta(0) and parsed.isoformat().replace("+00:00", "Z") == value

    validator = Draft202012Validator(schema, format_checker=format_checker)
    valid = event("e1", "payment.captured", "10:00")
    assert validator.is_valid(valid)
    for invalid in (
        {**valid, "cryptographically_verified": True},
        {**valid, "verification": {"status": "verified", "adapter": "none"}},
        {**valid, "verification": {"status": "unverified", "adapter": "none", "verified": True}},
        {**valid, "amount_minor": 1.5},
        {**valid, "amount_minor": 0},
        {**valid, "currency": "usd"},
        {**valid, "payment_ref": "https://example.test/payment"},
        {**valid, "occurred_at": "not-a-dateZ"},
        {**valid, "occurred_at": "2026-09-28T10:00:00.123456789Z"},
        {**valid, "occurred_at": "2026-99-40T10:00:00Z"},
        {**valid, "extra": "refused"},
    ):
        assert not validator.is_valid(invalid)


def test_validator_refuses_boolean_verification_claim() -> None:
    payload = event("e1", "payment.captured", "10:00")
    payload["verified"] = True
    with pytest.raises(MOR.InputError, match="unknown|verification"):
        MOR.validate_event(payload)


def test_exact_duplicate_is_idempotent_and_remains_unverified() -> None:
    captured = event("capture-1", "payment.captured", "10:00")
    result = MOR.reconcile([captured, dict(captured)], [order()], [])
    assert result["outcome"] == "clean"
    assert result["duplicate_count"] == 1
    assert result["payments"][0]["state"] == "captured"
    assert result["payments"][0]["verification_state"] == "unverified"


def test_same_event_id_with_changed_payload_is_a_conflict() -> None:
    first = event("capture-1", "payment.captured", "10:00")
    altered = {**first, "amount_minor": 999}
    result = MOR.reconcile([first, altered], [order()], [])
    assert result["outcome"] == "conflict"
    assert "event_id_collision" in {item["code"] for item in result["conflicts"]}


def test_same_provider_event_reference_with_changed_content_is_a_conflict() -> None:
    first = event("capture-1", "payment.captured", "10:00")
    second = {**first, "event_id": "capture-2", "amount_minor": 999}
    result = MOR.reconcile([first, second], [order()], [])
    assert "provider_event_ref_collision" in {item["code"] for item in result["conflicts"]}


def test_late_arriving_older_lifecycle_event_does_not_regress_capture() -> None:
    captured = event("capture", "payment.captured", "10:02")
    authorized = event("auth", "payment.authorized", "10:01")
    result = MOR.reconcile([captured, authorized], [order()], [])
    assert result["outcome"] == "clean"
    assert result["payments"][0]["state"] == "captured"


@pytest.mark.parametrize(
    ("expected", "captured"),
    [(1001, 1000), (1000, 1001)],
)
def test_order_and_capture_amount_must_match_exactly(expected: int, captured: int) -> None:
    result = MOR.reconcile(
        [event("capture", "payment.captured", "10:00", amount=captured)], [order(amount=expected)], []
    )
    assert result["outcome"] == "conflict"
    assert "amount_mismatch" in {item["code"] for item in result["conflicts"]}


def test_currency_must_match_exactly() -> None:
    result = MOR.reconcile([event("capture", "payment.captured", "10:00", currency="EUR")], [order()], [])
    assert result["outcome"] == "conflict"
    assert "currency_mismatch" in {item["code"] for item in result["conflicts"]}


def test_refunds_and_chargebacks_are_cumulative_and_bounded_by_capture() -> None:
    events = [
        event("capture", "payment.captured", "10:00"),
        event("refund", "payment.refunded", "10:01", amount=250),
        event("chargeback", "payment.chargeback", "10:02", amount=750),
    ]
    result = MOR.reconcile(events, [order()], [])
    assert result["outcome"] == "clean"
    assert result["payments"][0]["refunded_minor"] == 250
    assert result["payments"][0]["chargeback_minor"] == 750
    assert result["payments"][0]["net_minor"] == 0
    overdraw = MOR.reconcile(events + [event("refund-2", "payment.refunded", "10:03", amount=1)], [order()], [])
    assert overdraw["outcome"] == "conflict"
    assert "reversal_exceeds_capture" in {item["code"] for item in overdraw["conflicts"]}


def test_aggregate_refund_output_stays_within_safe_integer_bound() -> None:
    maximum = MOR._MAX_MINOR
    events = [
        event("capture", "payment.captured", "10:00", amount=maximum),
        event("refund-1", "payment.refunded", "10:01", amount=maximum),
        event("refund-2", "payment.refunded", "10:02", amount=maximum),
    ]
    with pytest.raises(MOR.InputError, match="cumulative refunded amount exceeds"):
        MOR.reconcile(events, [order(amount=maximum)], [])


def test_cumulative_payout_output_stays_within_safe_integer_bound() -> None:
    maximum = MOR._MAX_MINOR
    capture = event("capture", "payment.captured", "10:00", amount=maximum)
    allocation = {"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": maximum}
    payouts = [
        {"payout_ref": "p1", "currency": "USD", "amount_minor": maximum, "allocations": [allocation]},
        {"payout_ref": "p2", "currency": "USD", "amount_minor": maximum, "allocations": [allocation]},
    ]
    with pytest.raises(MOR.InputError, match="cumulative payout allocation exceeds"):
        MOR.reconcile([capture], [order(amount=maximum)], payouts)


def test_zero_value_capture_or_reversal_is_refused() -> None:
    for kind in ("payment.authorized", "payment.captured", "payment.refunded", "payment.chargeback"):
        with pytest.raises(MOR.InputError, match="positive"):
            MOR.validate_event(event("zero", kind, "10:00", amount=0))


def test_reversal_before_capture_is_a_conflict_even_if_arrival_is_late() -> None:
    events = [
        event("capture", "payment.captured", "10:02"),
        event("refund", "payment.refunded", "10:01", amount=200),
    ]
    result = MOR.reconcile(events, [order()], [])
    assert result["outcome"] == "conflict"
    assert "reversal_before_capture" in {item["code"] for item in result["conflicts"]}


def test_payout_allocations_must_tie_exactly_to_payout_and_captured_order() -> None:
    events = [event("capture", "payment.captured", "10:00")]
    payout = {
        "payout_ref": "payout:synthetic-1",
        "currency": "USD",
        "amount_minor": 1000,
        "allocations": [{"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1000}],
    }
    good = MOR.reconcile(events, [order()], [payout])
    assert good["outcome"] == "clean"
    assert good["payouts"][0]["state"] == "reconciled"
    assert good["payments"][0]["allocated_minor"] == 1000
    assert good["payments"][0]["unallocated_minor"] == 0
    bad_payout = {**payout, "amount_minor": 999}
    bad = MOR.reconcile(events, [order()], [bad_payout])
    assert bad["outcome"] == "conflict"
    assert "payout_allocation_mismatch" in {item["code"] for item in bad["conflicts"]}


def test_payout_overallocation_and_currency_mismatch_fail_closed() -> None:
    events = [event("capture", "payment.captured", "10:00")]
    allocation = {"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1001}
    payout = {"payout_ref": "p1", "currency": "USD", "amount_minor": 1001, "allocations": [allocation]}
    over = MOR.reconcile(events, [order()], [payout])
    assert "payout_exceeds_net_capture" in {item["code"] for item in over["conflicts"]}
    payout["currency"] = "EUR"
    mismatch = MOR.reconcile(events, [order()], [payout])
    assert "payout_currency_mismatch" in {item["code"] for item in mismatch["conflicts"]}


@pytest.mark.parametrize("unsupported", ["fee_minor", "fx_rate", "reserve_minor", "adjustment_minor", "reversal_minor"])
def test_payout_refuses_provider_specific_fees_fx_reserves_adjustments_and_reversals(unsupported: str) -> None:
    payout = {
        "payout_ref": "payout:synthetic-1",
        "currency": "USD",
        "amount_minor": 1000,
        "allocations": [{"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1000}],
        unsupported: 1,
    }
    with pytest.raises(MOR.InputError, match="unknown"):
        MOR.reconcile([event("capture", "payment.captured", "10:00")], [order()], [payout])


def test_negative_payout_allocation_is_refused() -> None:
    payout = {
        "payout_ref": "payout:synthetic-1",
        "currency": "USD",
        "amount_minor": 1,
        "allocations": [{"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": -1}],
    }
    with pytest.raises(MOR.InputError, match="positive"):
        MOR.reconcile([event("capture", "payment.captured", "10:00")], [order()], [payout])


def test_total_payout_allocations_are_bounded_for_direct_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MOR, "_MAX_TOTAL_ALLOCATIONS", 2)
    payout = {
        "payout_ref": "p1",
        "currency": "USD",
        "amount_minor": 1,
        "allocations": [
            {"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1},
            {"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1},
        ],
    }
    with pytest.raises(MOR.InputError, match="total at most 2 rows"):
        MOR.reconcile([], [order()], [payout, {**payout, "payout_ref": "p2"}])


def test_conflicting_payment_history_cannot_produce_reconciled_payout() -> None:
    events = [
        event("capture", "payment.captured", "10:00"),
        event("late-failure", "payment.failed", "10:01"),
    ]
    payout = {
        "payout_ref": "payout:synthetic-1",
        "currency": "USD",
        "amount_minor": 1000,
        "allocations": [{"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1000}],
    }
    result = MOR.reconcile(events, [order()], [payout])
    assert result["outcome"] == "conflict"
    assert result["payments"][0]["history_state"] == "conflict"
    assert result["payouts"][0]["state"] == "conflict"
    assert "payout_conflicted_payment_history" in {item["code"] for item in result["conflicts"]}


def test_event_identity_conflict_poisoning_payment_blocks_its_payout() -> None:
    captured = event("capture", "payment.captured", "10:00")
    conflicting = {**captured, "amount_minor": 999}
    payout = {
        "payout_ref": "payout:synthetic-1",
        "currency": "USD",
        "amount_minor": 1000,
        "allocations": [{"payment_ref": "payment:synthetic-1", "order_ref": "order:synthetic-1", "amount_minor": 1000}],
    }
    result = MOR.reconcile([captured, conflicting], [order()], [payout])
    assert result["outcome"] == "conflict"
    assert result["payouts"][0]["state"] == "conflict"


def test_unknown_lifecycle_transition_is_a_conflict_not_a_state_change() -> None:
    result = MOR.reconcile(
        [event("failed", "payment.failed", "10:00"), event("capture", "payment.captured", "10:01")], [order()], []
    )
    assert result["outcome"] == "conflict"
    assert "lifecycle_conflict" in {item["code"] for item in result["conflicts"]}


def test_candidate_has_no_provider_adapter_or_external_io_imports() -> None:
    import ast

    tree = ast.parse((ROOT / "candidate.py").read_text())
    imports = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imports.isdisjoint({"http", "httpx", "requests", "socket", "urllib", "subprocess", "sqlite3", "psycopg2"})


def test_checked_in_synthetic_example_is_read_only_and_reconciles(capsys) -> None:
    example_path = ROOT / "examples" / "reconciliation.synthetic.json"
    before = example_path.read_bytes()
    assert MOR.main([str(example_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "clean"
    assert result["payments"][0]["verification_state"] == "unverified"
    assert example_path.read_bytes() == before


def test_cli_duplicate_json_keys_fail_without_traceback(tmp_path, capsys) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"contract_version":"merchant_reconciliation_input.v1","contract_version":"merchant_reconciliation_input.v1"}'
    )
    assert MOR.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert "duplicate JSON key" in captured.err
    assert "Traceback" not in captured.err


def test_cli_oversized_integer_fails_without_traceback(tmp_path, capsys) -> None:
    path = tmp_path / "oversized-integer.json"
    path.write_text(
        '{"contract_version":"merchant_reconciliation_input.v1","events":[],"orders":[{"order_ref":"o1","amount_minor":'
        + "9" * 5000
        + ',"currency":"USD"}],"payouts":[]}'
    )
    assert MOR.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert "safe-integer bound" in captured.err
    assert "Traceback" not in captured.err
