#!/usr/bin/env python3
"""Reconcile an explicitly supplied monthly budget and invoice allocation snapshot.

Offline and read-only: performs arithmetic over caller-normalized amounts in one currency. It does
not authenticate invoice references, verify source records, connect to accounting systems, or make
financial recommendations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PERIOD = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
_MAX_INPUT_BYTES = 1_048_576
_MAX_DECIMAL_CHARS = 64
_MAX_FRACTIONAL_DIGITS = _MAX_DECIMAL_CHARS - 2  # `0.` plus 62 fractional digits.
_SCALE_FACTOR = 10**_MAX_FRACTIONAL_DIGITS
_MAX_JSON_INTEGER_DIGITS = 64
_MAX_ENTITIES = 50
_MAX_INVOICES = 2_000


class InputError(ValueError):
    """Input is invalid or outside the local reconciliation contract."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        unknown_count = len(set(value) - keys)
        # Missing names come from the trusted contract; unknown keys are untrusted and never echoed.
        raise InputError(f"{where} keys invalid (missing={missing}, unknown={unknown_count})")
    return value


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InputError(f"{where} must be a safe lowercase identifier")
    return value


def _amount(value: Any, where: str, *, positive: bool = False) -> tuple[int, str, int]:
    if not isinstance(value, str) or len(value) > _MAX_DECIMAL_CHARS or not _DECIMAL.fullmatch(value):
        raise InputError(f"{where} must be a non-negative decimal string of at most {_MAX_DECIMAL_CHARS} characters")
    whole, separator, fraction = value.partition(".")
    places = len(fraction) if separator else 0
    scaled = int(whole) * _SCALE_FACTOR
    if fraction:
        scaled += int(fraction) * 10 ** (_MAX_FRACTIONAL_DIGITS - places)
    if positive and scaled <= 0:
        raise InputError(f"{where} must be positive")
    return scaled, value, places


def _opaque_reference(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value) or "://" in value:
        raise InputError(f"{where} must be an opaque invoice reference, not a URL or free text")
    return value


def _date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date")
    return parsed


def _money(value: int, places: int) -> str:
    """Format a scaled integer without Decimal context rounding."""
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), _SCALE_FACTOR)
    if places == 0:
        return f"{sign}{whole}"
    fractional_text = str(fraction).zfill(_MAX_FRACTIONAL_DIGITS)[:places]
    return f"{sign}{whole}.{fractional_text}"


def reconcile_month(payload: Any) -> dict[str, Any]:
    """Validate one budget snapshot and return deterministic per-entity variance totals."""
    root = _object(payload, "budget", {"schema_version", "period", "currency", "entities", "invoices"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("budget.schema_version must be integer 1")
    period = root["period"]
    if not isinstance(period, str) or not _PERIOD.fullmatch(period):
        raise InputError("budget.period must be canonical YYYY-MM")
    currency = root["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise InputError("budget.currency must be a three-letter uppercase currency code")

    raw_entities = root["entities"]
    if type(raw_entities) is not list or not 1 <= len(raw_entities) <= _MAX_ENTITIES:
        raise InputError(f"budget.entities must contain 1..{_MAX_ENTITIES} entities")
    budgets: dict[str, tuple[int, str, int]] = {}
    actuals: dict[str, int] = {}
    actual_places: dict[str, int] = {}
    for index, raw_entity in enumerate(raw_entities):
        where = f"entities[{index}]"
        entity = _object(raw_entity, where, {"entity_id", "budget_amount"})
        entity_id = _identifier(entity["entity_id"], f"{where}.entity_id")
        if entity_id in budgets:
            raise InputError("entity_id values must be unique")
        budgets[entity_id] = _amount(entity["budget_amount"], f"{where}.budget_amount")
        actuals[entity_id] = 0
        actual_places[entity_id] = 0

    invoices = root["invoices"]
    if type(invoices) is not list or len(invoices) > _MAX_INVOICES:
        raise InputError(f"budget.invoices must contain 0..{_MAX_INVOICES} invoices")
    invoice_refs: set[str] = set()
    invoice_total = 0
    invoice_total_places = 0
    allocated_total = 0
    allocated_total_places = 0
    for invoice_index, raw_invoice in enumerate(invoices):
        where = f"invoices[{invoice_index}]"
        invoice = _object(raw_invoice, where, {"invoice_ref", "invoice_date", "amount", "allocations"})
        ref = _opaque_reference(invoice["invoice_ref"], f"{where}.invoice_ref")
        if ref in invoice_refs:
            raise InputError("invoice_ref values must be unique")
        invoice_refs.add(ref)
        invoice_date = _date(invoice["invoice_date"], f"{where}.invoice_date")
        if invoice_date.isoformat()[:7] != period:
            raise InputError(f"{where}.invoice_date must fall within budget.period")
        amount, _, amount_places = _amount(invoice["amount"], f"{where}.amount", positive=True)
        invoice_total += amount
        invoice_total_places = max(invoice_total_places, amount_places)

        allocations = invoice["allocations"]
        if type(allocations) is not list or not 1 <= len(allocations) <= len(budgets):
            raise InputError(f"{where}.allocations must contain 1..{len(budgets)} entity allocations")
        allocation_total = 0
        allocated_entities: set[str] = set()
        for allocation_index, raw_allocation in enumerate(allocations):
            allocation_where = f"{where}.allocations[{allocation_index}]"
            allocation = _object(raw_allocation, allocation_where, {"entity_id", "amount"})
            entity_id = _identifier(allocation["entity_id"], f"{allocation_where}.entity_id")
            if entity_id not in budgets:
                raise InputError(f"{allocation_where}.entity_id has no monthly budget row")
            if entity_id in allocated_entities:
                raise InputError(f"{where} must not allocate twice to one entity")
            allocated_entities.add(entity_id)
            share, _, share_places = _amount(allocation["amount"], f"{allocation_where}.amount", positive=True)
            allocation_total += share
            actuals[entity_id] += share
            actual_places[entity_id] = max(actual_places[entity_id], share_places)
            allocated_total += share
            allocated_total_places = max(allocated_total_places, share_places)
        if allocation_total != amount:
            raise InputError(f"{where}.allocations must sum exactly to invoice amount")

    entity_results: list[dict[str, str]] = []
    total_budget = 0
    total_budget_places = 0
    total_variance = 0
    total_variance_places = 0
    for entity_id in sorted(budgets):
        budget, budget_text, budget_places = budgets[entity_id]
        actual = actuals[entity_id]
        variance = budget - actual
        total_budget += budget
        total_budget_places = max(total_budget_places, budget_places)
        total_variance += variance
        total_variance_places = max(total_variance_places, budget_places, actual_places[entity_id])
        entity_results.append(
            {
                "entity_id": entity_id,
                "budget_amount": budget_text,
                "invoice_total": _money(actual, actual_places[entity_id]),
                "variance_amount": _money(variance, max(budget_places, actual_places[entity_id])),
                "budget_position": "within_budget" if variance >= 0 else "over_budget",
            }
        )
    return {
        "schema_version": 1,
        "period": period,
        "currency": currency,
        "entities": entity_results,
        "invoice_count": len(invoices),
        "unallocated_amount": _money(
            invoice_total - allocated_total, max(invoice_total_places, allocated_total_places)
        ),
        "total_budget": _money(total_budget, total_budget_places),
        "total_invoice_amount": _money(invoice_total, invoice_total_places),
        "total_variance": _money(total_variance, total_variance_places),
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key")
        result[key] = value
    return result


def _parse_json_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > _MAX_JSON_INTEGER_DIGITS:
        raise InputError(f"JSON integer must not exceed {_MAX_JSON_INTEGER_DIGITS} digits")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="explicit JSON budget input path")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise InputError(f"input file must not exceed {_MAX_INPUT_BYTES} bytes")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_parse_json_integer,
        )
        result = reconcile_month(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InputError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
