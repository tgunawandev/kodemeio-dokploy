#!/usr/bin/env python3
"""Offline, provider-neutral R7/PAY2 event and payout reconciliation candidate."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_EVENT_TYPES = {
    "payment.pending",
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "payment.cancelled",
    "payment.refunded",
    "payment.chargeback",
}
_EVENT_KEYS = {
    "contract_version",
    "event_id",
    "provider_event_ref",
    "event_type",
    "occurred_at",
    "order_ref",
    "payment_ref",
    "amount_minor",
    "currency",
    "verification",
}
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_INPUT_BYTES = 1_048_576
_MAX_EVENTS = 10_000
_MAX_ORDERS = 10_000
_MAX_PAYOUTS = 2_000
_MAX_ALLOCATIONS = 10_000
_MAX_MINOR = 9_007_199_254_740_991


class InputError(ValueError):
    """Input is malformed or outside this intentionally bounded contract."""


def _exact_object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    missing, unknown = sorted(keys - value.keys()), sorted(value.keys() - keys)
    if missing or unknown:
        raise InputError(f"{where} keys invalid (missing={missing}, unknown={unknown})")
    return value


def _reference(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value) or "://" in value:
        raise InputError(f"{where} must be an opaque reference")
    return value


def _minor(value: Any, where: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > _MAX_MINOR:
        qualifier = "positive" if positive else "non-negative"
        raise InputError(f"{where} must be a {qualifier} integer minor-unit amount within the safe bound")
    return value


def _currency(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise InputError(f"{where} must be a three-letter uppercase currency code")
    return value


def validate_event(value: Any) -> dict[str, Any]:
    """Validate one normalized event; no field can declare it cryptographically verified."""
    event = _exact_object(value, "event", _EVENT_KEYS)
    if event["contract_version"] != "merchant_event.v1":
        raise InputError("event.contract_version must be merchant_event.v1")
    for key in ("event_id", "provider_event_ref", "order_ref", "payment_ref"):
        _reference(event[key], f"event.{key}")
    if not isinstance(event["event_type"], str) or event["event_type"] not in _EVENT_TYPES:
        raise InputError("event.event_type is unsupported")
    timestamp = event["occurred_at"]
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise InputError("event.occurred_at must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise InputError("event.occurred_at must be a valid RFC3339 UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.isoformat().replace("+00:00", "Z") != timestamp:
        raise InputError("event.occurred_at must use canonical UTC timestamp syntax")
    _minor(event["amount_minor"], "event.amount_minor")
    if event["event_type"] in {"payment.authorized", "payment.captured", "payment.refunded", "payment.chargeback"}:
        _minor(event["amount_minor"], "event.amount_minor", positive=True)
    _currency(event["currency"], "event.currency")
    verification = _exact_object(event["verification"], "event.verification", {"status", "adapter"})
    if verification != {"status": "unverified", "adapter": "none"}:
        raise InputError("event.verification is fixed to status=unverified and adapter=none")
    return dict(event)


def _validate_order(value: Any, index: int) -> dict[str, Any]:
    order = _exact_object(value, f"orders[{index}]", {"order_ref", "amount_minor", "currency"})
    _reference(order["order_ref"], f"orders[{index}].order_ref")
    _minor(order["amount_minor"], f"orders[{index}].amount_minor", positive=True)
    _currency(order["currency"], f"orders[{index}].currency")
    return dict(order)


def _validate_payout(value: Any, index: int) -> dict[str, Any]:
    where = f"payouts[{index}]"
    payout = _exact_object(value, where, {"payout_ref", "currency", "amount_minor", "allocations"})
    _reference(payout["payout_ref"], f"{where}.payout_ref")
    _currency(payout["currency"], f"{where}.currency")
    _minor(payout["amount_minor"], f"{where}.amount_minor", positive=True)
    allocations = payout["allocations"]
    if type(allocations) is not list or not allocations or len(allocations) > _MAX_ALLOCATIONS:
        raise InputError(f"{where}.allocations must contain 1..{_MAX_ALLOCATIONS} rows")
    checked = []
    for allocation_index, raw in enumerate(allocations):
        allocation_where = f"{where}.allocations[{allocation_index}]"
        allocation = _exact_object(raw, allocation_where, {"payment_ref", "order_ref", "amount_minor"})
        _reference(allocation["payment_ref"], f"{allocation_where}.payment_ref")
        _reference(allocation["order_ref"], f"{allocation_where}.order_ref")
        _minor(allocation["amount_minor"], f"{allocation_where}.amount_minor", positive=True)
        checked.append(dict(allocation))
    return {**payout, "allocations": checked}


def _conflict(code: str, reference: str, detail: str) -> dict[str, str]:
    return {"code": code, "reference": reference, "detail": detail}


def reconcile(events: Any, orders: Any, payouts: Any) -> dict[str, Any]:
    """Replay normalized synthetic events and tie payout rows to net captured amounts."""
    if type(events) is not list or len(events) > _MAX_EVENTS:
        raise InputError(f"events must be a list with at most {_MAX_EVENTS} entries")
    if type(orders) is not list or not 1 <= len(orders) <= _MAX_ORDERS:
        raise InputError(f"orders must contain 1..{_MAX_ORDERS} entries")
    if type(payouts) is not list or len(payouts) > _MAX_PAYOUTS:
        raise InputError(f"payouts must be a list with at most {_MAX_PAYOUTS} entries")

    conflicts: list[dict[str, str]] = []
    order_by_ref: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(orders):
        row = _validate_order(raw, index)
        if row["order_ref"] in order_by_ref:
            raise InputError("order_ref values must be unique")
        order_by_ref[row["order_ref"]] = row

    unique_events: dict[str, dict[str, Any]] = {}
    provider_refs: dict[str, dict[str, Any]] = {}
    poisoned_payment_refs: set[str] = set()
    duplicate_count = 0
    for raw in events:
        event = validate_event(raw)
        event_id = event["event_id"]
        prior = unique_events.get(event_id)
        if prior is None:
            provider_ref = event["provider_event_ref"]
            provider_prior = provider_refs.get(provider_ref)
            if provider_prior is not None:
                conflicts.append(
                    _conflict(
                        "provider_event_ref_collision",
                        provider_ref,
                        "provider_event_ref was reused; event identity is ambiguous",
                    )
                )
                poisoned_payment_refs.update((provider_prior["payment_ref"], event["payment_ref"]))
                continue
            unique_events[event_id] = event
            provider_refs[provider_ref] = event
        elif prior == event:
            duplicate_count += 1
        else:
            conflicts.append(
                _conflict("event_id_collision", event_id, "same event_id has different normalized content")
            )
            poisoned_payment_refs.update((prior["payment_ref"], event["payment_ref"]))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in unique_events.values():
        grouped.setdefault(event["payment_ref"], []).append(event)

    payment_rows: list[dict[str, Any]] = []
    payment_lookup: dict[str, dict[str, Any]] = {}
    for payment_ref, history in sorted(grouped.items()):
        history_conflict_start = len(conflicts)
        history.sort(key=lambda item: (item["occurred_at"], item["event_id"]))
        if payment_ref in poisoned_payment_refs:
            conflicts.append(
                _conflict(
                    "payment_event_identity_conflict",
                    payment_ref,
                    "payment history contains an event identity collision",
                )
            )
        order_refs = {item["order_ref"] for item in history}
        if len(order_refs) != 1:
            conflicts.append(
                _conflict("payment_order_conflict", payment_ref, "payment events refer to multiple orders")
            )
        order_ref = history[0]["order_ref"]
        order_row = order_by_ref.get(order_ref)
        if order_row is None:
            conflicts.append(_conflict("unknown_order", payment_ref, "payment has no matching order row"))
        state: str | None = None
        captured_amount: int | None = None
        captured_currency: str | None = None
        refunded = 0
        chargeback = 0
        seen_capture = False
        for item in history:
            kind = item["event_type"]
            amount = item["amount_minor"]
            currency = item["currency"]
            if order_row is not None and kind in {"payment.authorized", "payment.captured"}:
                if amount != order_row["amount_minor"]:
                    conflicts.append(
                        _conflict("amount_mismatch", payment_ref, f"{kind} amount differs from exact order amount")
                    )
                if currency != order_row["currency"]:
                    conflicts.append(
                        _conflict("currency_mismatch", payment_ref, f"{kind} currency differs from order currency")
                    )
            if kind == "payment.pending":
                if state is None:
                    state = "pending"
                elif state not in {"pending", "authorized"}:
                    conflicts.append(
                        _conflict("lifecycle_conflict", payment_ref, "pending event follows a terminal lifecycle state")
                    )
            elif kind == "payment.authorized":
                if state in {None, "pending", "authorized"}:
                    state = "authorized"
                else:
                    conflicts.append(
                        _conflict(
                            "lifecycle_conflict", payment_ref, "authorization conflicts with current lifecycle state"
                        )
                    )
            elif kind == "payment.captured":
                if state in {None, "pending", "authorized"} and not seen_capture:
                    state = "captured"
                    captured_amount, captured_currency, seen_capture = amount, currency, True
                else:
                    conflicts.append(
                        _conflict(
                            "lifecycle_conflict",
                            payment_ref,
                            "capture conflicts with or duplicates a terminal lifecycle state",
                        )
                    )
            elif kind in {"payment.failed", "payment.cancelled"}:
                target = "failed" if kind == "payment.failed" else "cancelled"
                if state in {None, "pending", "authorized"}:
                    state = target
                else:
                    conflicts.append(
                        _conflict(
                            "lifecycle_conflict", payment_ref, f"{target} event conflicts with current lifecycle state"
                        )
                    )
            else:
                if captured_amount is None:
                    conflicts.append(
                        _conflict("reversal_before_capture", payment_ref, "refund or chargeback has no earlier capture")
                    )
                elif currency != captured_currency:
                    conflicts.append(
                        _conflict("currency_mismatch", payment_ref, "reversal currency differs from captured currency")
                    )
                elif kind == "payment.refunded":
                    refunded += amount
                else:
                    chargeback += amount
                if captured_amount is not None and refunded + chargeback > captured_amount:
                    conflicts.append(
                        _conflict(
                            "reversal_exceeds_capture",
                            payment_ref,
                            "cumulative refunds and chargebacks exceed captured amount",
                        )
                    )
        net = max(0, (captured_amount or 0) - refunded - chargeback)
        if chargeback:
            final_state = "chargeback"
        elif refunded:
            final_state = "refunded" if net == 0 else "partially_refunded"
        else:
            final_state = state or "unobserved"
        history_state = "conflict" if len(conflicts) > history_conflict_start else "consistent"
        payment = {
            "payment_ref": payment_ref,
            "order_ref": order_ref,
            "state": "conflict" if history_state == "conflict" else final_state,
            "observed_state": final_state,
            "history_state": history_state,
            "captured_minor": captured_amount or 0,
            "refunded_minor": refunded,
            "chargeback_minor": chargeback,
            "net_minor": net,
            "currency": captured_currency or (order_row["currency"] if order_row else history[-1]["currency"]),
            "verification_state": "unverified",
        }
        payment_rows.append(payment)
        payment_lookup[payment_ref] = payment

    payout_rows: list[dict[str, Any]] = []
    payout_refs: set[str] = set()
    allocated_by_payment: dict[str, int] = {}
    for index, raw in enumerate(payouts):
        payout = _validate_payout(raw, index)
        payout_ref = payout["payout_ref"]
        if payout_ref in payout_refs:
            raise InputError("payout_ref values must be unique")
        payout_refs.add(payout_ref)
        allocation_total = sum(row["amount_minor"] for row in payout["allocations"])
        payout_has_conflict = False
        if allocation_total != payout["amount_minor"]:
            conflicts.append(
                _conflict("payout_allocation_mismatch", payout_ref, "allocation sum differs from payout amount")
            )
            payout_has_conflict = True
        for allocation in payout["allocations"]:
            payment = payment_lookup.get(allocation["payment_ref"])
            if payment is None or payment["order_ref"] != allocation["order_ref"] or not payment["captured_minor"]:
                conflicts.append(
                    _conflict(
                        "payout_unmatched_payment",
                        payout_ref,
                        "allocation does not identify a captured payment and its order",
                    )
                )
                payout_has_conflict = True
                continue
            if payment["history_state"] == "conflict":
                conflicts.append(
                    _conflict(
                        "payout_conflicted_payment_history",
                        payout_ref,
                        "allocation references a payment with a conflicted event history",
                    )
                )
                payout_has_conflict = True
            if payout["currency"] != payment["currency"]:
                conflicts.append(
                    _conflict(
                        "payout_currency_mismatch", payout_ref, "payout currency differs from captured payment currency"
                    )
                )
                payout_has_conflict = True
            total_allocated = allocated_by_payment.get(payment["payment_ref"], 0) + allocation["amount_minor"]
            if total_allocated > payment["net_minor"]:
                conflicts.append(
                    _conflict(
                        "payout_exceeds_net_capture",
                        payout_ref,
                        "cumulative payout allocation exceeds net captured amount",
                    )
                )
                payout_has_conflict = True
            allocated_by_payment[payment["payment_ref"]] = total_allocated
        payout_rows.append(
            {
                "payout_ref": payout_ref,
                "amount_minor": payout["amount_minor"],
                "currency": payout["currency"],
                "state": "conflict" if payout_has_conflict else "reconciled",
            }
        )

    for payment in payment_rows:
        allocated = allocated_by_payment.get(payment["payment_ref"], 0)
        payment["allocated_minor"] = allocated
        payment["unallocated_minor"] = max(0, payment["net_minor"] - allocated)

    return {
        "contract_version": "merchant_reconciliation.v1",
        "outcome": "conflict" if conflicts else "clean",
        "duplicate_count": duplicate_count,
        "payments": payment_rows,
        "payouts": payout_rows,
        "conflicts": conflicts,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="explicit path to an offline synthetic reconciliation JSON file")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise InputError(f"input file must not exceed {_MAX_INPUT_BYTES} bytes")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        root = _exact_object(payload, "input", {"contract_version", "events", "orders", "payouts"})
        if root["contract_version"] != "merchant_reconciliation_input.v1":
            raise InputError("input.contract_version must be merchant_reconciliation_input.v1")
        result = reconcile(root["events"], root["orders"], root["payouts"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InputError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if result["outcome"] == "conflict" else 0


if __name__ == "__main__":
    raise SystemExit(main())
