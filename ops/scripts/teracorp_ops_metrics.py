#!/usr/bin/env python3
"""Validate local G2 product scorecards and summarize G3 founder-hours logs.

This is an offline, read-only operator utility. It makes no product decisions and never reads
production services or writes files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_UNIT = re.compile(r"^[a-z][a-z0-9_/-]{0,31}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_EVIDENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
_WEEK_MINUTES = 7 * 24 * 60
_MAX_PRODUCTS = 100
_MAX_METRICS = 50
_MAX_WEEKS = 104
_MAX_INPUT_BYTES = 1_048_576
_MAX_JSON_DEPTH = 64
_MAX_JSON_INTEGER_DIGITS = 64
_MAX_DECIMAL_CHARS = 64


class InputError(ValueError):
    """Input is invalid or outside the offline metrics contract."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        unknown = sorted(set(value) - keys)
        raise InputError(f"{where} keys invalid (missing={missing}, unknown={unknown})")
    return value


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InputError(f"{where} must be a safe lowercase identifier")
    return value


def _iso_date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise InputError(f"{where} must be a canonical YYYY-MM-DD date")
    return parsed


def _decimal(value: Any, where: str, *, nonnegative: bool) -> Decimal:
    if not isinstance(value, str) or len(value) > _MAX_DECIMAL_CHARS or not _DECIMAL.fullmatch(value):
        raise InputError(f"{where} must be a decimal string of at most {_MAX_DECIMAL_CHARS} characters")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InputError(f"{where} must be a decimal string") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise InputError(f"{where} must be finite and non-negative")
    return parsed


def _evidence_ref(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _EVIDENCE.fullmatch(value) or "://" in value:
        raise InputError(f"{where} must be an opaque evidence reference, not a URL or free text")
    return value


def evaluate_scorecards(payload: Any) -> dict[str, Any]:
    """Validate scorecards and compare only measured values to caller-defined thresholds."""
    root = _object(payload, "scorecards", {"schema_version", "as_of_date", "products"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("scorecards.schema_version must be integer 1")
    as_of_date = _iso_date(root["as_of_date"], "scorecards.as_of_date")
    products = root["products"]
    if type(products) is not list or not 1 <= len(products) <= _MAX_PRODUCTS:
        raise InputError(f"scorecards.products must contain 1..{_MAX_PRODUCTS} products")

    seen_products: set[str] = set()
    output_products: list[dict[str, Any]] = []
    for product_index, raw_product in enumerate(products):
        where = f"products[{product_index}]"
        product = _object(
            raw_product,
            where,
            {"product_id", "launch_date", "currency", "metrics", "checkpoints"},
        )
        product_id = _identifier(product["product_id"], f"{where}.product_id")
        if product_id in seen_products:
            raise InputError("product_id values must be unique")
        seen_products.add(product_id)
        launch_date = _iso_date(product["launch_date"], f"{where}.launch_date")
        currency = product["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise InputError(f"{where}.currency must be a three-letter uppercase currency code")

        raw_metrics = product["metrics"]
        if type(raw_metrics) is not list or not 1 <= len(raw_metrics) <= _MAX_METRICS:
            raise InputError(f"{where}.metrics must contain 1..{_MAX_METRICS} metrics")
        metrics: dict[str, tuple[str, str, Decimal, str]] = {}
        for metric_index, raw_metric in enumerate(raw_metrics):
            metric_where = f"{where}.metrics[{metric_index}]"
            metric = _object(
                raw_metric,
                metric_where,
                {"metric_id", "direction", "unit", "threshold"},
            )
            metric_id = _identifier(metric["metric_id"], f"{metric_where}.metric_id")
            if metric_id in metrics:
                raise InputError(f"{where}.metric_id values must be unique")
            direction = metric["direction"]
            if not isinstance(direction, str) or direction not in {"gte", "lte"}:
                raise InputError(f"{metric_where}.direction must be gte or lte")
            unit = metric["unit"]
            if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
                raise InputError(f"{metric_where}.unit must be a safe lowercase unit identifier")
            threshold = _decimal(metric["threshold"], f"{metric_where}.threshold", nonnegative=True)
            metrics[metric_id] = (direction, unit, threshold, metric["threshold"])

        raw_checkpoints = product["checkpoints"]
        if type(raw_checkpoints) is not list or len(raw_checkpoints) != 2:
            raise InputError(f"{where}.checkpoints must contain exactly day 60 and day 90")
        checkpoint_results: list[dict[str, Any]] = []
        days: set[int] = set()
        for checkpoint_index, raw_checkpoint in enumerate(raw_checkpoints):
            checkpoint_where = f"{where}.checkpoints[{checkpoint_index}]"
            checkpoint = _object(
                raw_checkpoint,
                checkpoint_where,
                {"day", "due_date", "observed_on", "observations", "evidence_ref"},
            )
            day = checkpoint["day"]
            if type(day) is not int or day not in {60, 90} or day in days:
                raise InputError(f"{checkpoint_where}.day must uniquely be 60 and 90")
            days.add(day)
            due = launch_date + timedelta(days=day)
            if _iso_date(checkpoint["due_date"], f"{checkpoint_where}.due_date") != due:
                raise InputError(f"{checkpoint_where}.due_date must match launch_date + {day} days")
            observed_on_value = checkpoint["observed_on"]
            observed_on = (
                None if observed_on_value is None else _iso_date(observed_on_value, f"{checkpoint_where}.observed_on")
            )
            if observed_on is not None and observed_on < due:
                raise InputError(f"{checkpoint_where}.observed_on cannot precede its due date")
            if observed_on is not None and observed_on > as_of_date:
                raise InputError(f"{checkpoint_where}.observed_on cannot be after scorecards.as_of_date")
            observations = _object(
                checkpoint["observations"],
                f"{checkpoint_where}.observations",
                set(metrics),
            )
            has_observation = any(value is not None for value in observations.values())
            evidence_value = checkpoint["evidence_ref"]
            if has_observation:
                if observed_on is None or evidence_value is None:
                    raise InputError(f"{checkpoint_where} observations require observed_on and evidence_ref")
                _evidence_ref(evidence_value, f"{checkpoint_where}.evidence_ref")
            elif observed_on is not None or evidence_value is not None:
                raise InputError(f"{checkpoint_where} without observations must not claim evidence")

            evaluated: dict[str, dict[str, Any]] = {}
            for metric_id, observed_value in observations.items():
                direction, unit, threshold, threshold_text = metrics[metric_id]
                if observed_value is None:
                    evaluated[metric_id] = {
                        "status": "unmeasured",
                        "observed": None,
                        "direction": direction,
                        "unit": unit,
                        "threshold": threshold_text,
                    }
                    continue
                observed = _decimal(observed_value, f"{checkpoint_where}.{metric_id}", nonnegative=False)
                met = observed >= threshold if direction == "gte" else observed <= threshold
                evaluated[metric_id] = {
                    "status": "met" if met else "missed",
                    "observed": observed_value,
                    "direction": direction,
                    "unit": unit,
                    "threshold": threshold_text,
                }
            checkpoint_results.append(
                {
                    "day": day,
                    "due_date": due.isoformat(),
                    "observed_on": observed_on.isoformat() if observed_on else None,
                    "evidence_ref": evidence_value,
                    "metrics": evaluated,
                }
            )
        if days != {60, 90}:
            raise InputError(f"{where}.checkpoints must contain exactly day 60 and day 90")
        checkpoint_results.sort(key=lambda row: row["day"])
        output_products.append(
            {
                "product_id": product_id,
                "launch_date": launch_date.isoformat(),
                "currency": currency,
                "checkpoints": checkpoint_results,
            }
        )
    output_products.sort(key=lambda row: row["product_id"])
    return {"schema_version": 1, "as_of_date": as_of_date.isoformat(), "products": output_products}


def summarize_founder_hours(payload: Any) -> dict[str, Any]:
    """Summarize aggregate weekly minutes; only a consecutive four-week tail forms a trend."""
    root = _object(payload, "founder_hours", {"schema_version", "weeks"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("founder_hours.schema_version must be integer 1")
    weeks = root["weeks"]
    if type(weeks) is not list or not 1 <= len(weeks) <= _MAX_WEEKS:
        raise InputError(f"founder_hours.weeks must contain 1..{_MAX_WEEKS} weekly records")

    counters = ("approval_minutes", "outage_response_minutes", "manual_fulfilment_minutes")
    parsed: dict[date, dict[str, int]] = {}
    for index, raw_week in enumerate(weeks):
        where = f"weeks[{index}]"
        week = _object(raw_week, where, {"week_start", *counters})
        start = _iso_date(week["week_start"], f"{where}.week_start")
        if start.weekday() != 0:
            raise InputError(f"{where}.week_start must be a Monday")
        if start in parsed:
            raise InputError("week_start values must be unique")
        values: dict[str, int] = {}
        for counter in counters:
            value = week[counter]
            if type(value) is not int or not 0 <= value <= _WEEK_MINUTES:
                raise InputError(f"{where}.{counter} must be an integer from 0 to {_WEEK_MINUTES}")
            values[counter] = value
        if sum(values.values()) > _WEEK_MINUTES:
            raise InputError(f"{where} total minutes cannot exceed one week")
        parsed[start] = values

    ordered = sorted(parsed.items())
    output_weeks = []
    for index, (start, values) in enumerate(ordered):
        total = sum(values.values())
        prior = ordered[index - 1] if index else None
        delta = total - sum(prior[1].values()) if prior and start - prior[0] == timedelta(days=7) else None
        output_weeks.append(
            {
                "week_start": start.isoformat(),
                **values,
                "total_minutes": total,
                "change_vs_previous_week_minutes": delta,
            }
        )
    tail = ordered[-4:]
    consecutive = len(tail) == 4 and all(
        tail[index][0] - tail[index - 1][0] == timedelta(days=7) for index in range(1, len(tail))
    )
    return {
        "schema_version": 1,
        "trend_status": "four_consecutive_weeks" if consecutive else "insufficient_consecutive_weeks",
        "four_week_total_minutes": sum(sum(values.values()) for _, values in tail) if consecutive else None,
        "four_week_change_minutes": (sum(tail[-1][1].values()) - sum(tail[0][1].values()) if consecutive else None),
        "weeks": output_weeks,
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


def _enforce_json_depth(text: str) -> None:
    """Bound JSON container nesting, ignoring bracket characters inside strings."""
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise InputError(f"input exceeds the {_MAX_JSON_DEPTH}-level JSON nesting limit")
        elif character in "]}" and depth:
            depth -= 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate-scorecards", "validate G2 scorecards and evaluate explicit thresholds"),
        ("hours-trend", "summarize G3 aggregate weekly minutes"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument("input", type=Path, help="explicit JSON input path")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            raw_bytes = stream.read(_MAX_INPUT_BYTES + 1)
        if len(raw_bytes) > _MAX_INPUT_BYTES:
            raise InputError(f"input file must not exceed {_MAX_INPUT_BYTES} bytes")
        try:
            raw = raw_bytes.decode("utf-8")
            _enforce_json_depth(raw)
            payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_int=_parse_json_integer)
        except InputError:
            raise
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise InputError("input must be valid bounded UTF-8 JSON") from exc
        result = (
            evaluate_scorecards(payload) if args.command == "validate-scorecards" else summarize_founder_hours(payload)
        )
    except (OSError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
