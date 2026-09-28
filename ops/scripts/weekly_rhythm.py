#!/usr/bin/env python3
"""Build a deterministic offline founder rhythm packet from explicit OPS2/OPS3/OPS4 JSON summaries."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

_MAX_INPUT_BYTES = 1_048_576
_MAX_INTEGER_TOKEN_CHARS = 20
_MAX_AGE_DAYS = {"g2": 14, "g3": 10, "g4": 45}
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_EVIDENCE = re.compile(r"^(?:synthetic:(?:60d|day-60)|evidence:[0-9a-f]{32})$")
_PROVENANCE_NOTES = {"synthetic_fixture", "founder_supplied", "system_export", "unspecified"}
_RHYTHM = {
    "monday_priorities": "Founder writes priorities manually; no priority is inferred.",
    "daily_approval_batch_minutes": {"minimum": 20, "maximum": 30},
    "daily_approval_batch": (
        "Founder reviews pending approvals and records decisions in the owning system; "
        "this packet does not approve or execute."
    ),
    "friday_numbers": ["G2 product scorecards", "G3 founder-hours", "G4 budget/invoice reconciliation"],
    "friday_review": "Founder reviews freshness, gaps, and source evidence; this packet makes no recommendation.",
}


class InputError(ValueError):
    """Input is invalid or outside the bounded local OPS1 contract."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        raise InputError(f"{where} keys invalid")
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


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON object key")
        result[key] = value
    return result


def _json_integer(token: str) -> int:
    """Reject oversized integer tokens before Python's int conversion."""
    if len(token.lstrip("-")) > _MAX_INTEGER_TOKEN_CHARS:
        raise InputError("JSON integer token exceeds the supported bound")
    try:
        return int(token)
    except ValueError as exc:
        raise InputError("JSON integer token is invalid or exceeds the runtime bound") from exc


def _read_json(path: Path, where: str) -> Any:
    if path.suffix.lower() != ".json":
        raise InputError(f"{where} must name an explicit .json file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise InputError(f"{where} must be a regular file")
            chunks = bytearray()
            while len(chunks) <= _MAX_INPUT_BYTES:
                block = os.read(fd, min(65_536, _MAX_INPUT_BYTES + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
        finally:
            os.close(fd)
    except OSError as exc:
        raise InputError(f"cannot read {where}: {exc.strerror or 'invalid file'}") from exc
    if len(chunks) > _MAX_INPUT_BYTES:
        raise InputError(f"{where} must not exceed {_MAX_INPUT_BYTES} bytes")
    try:
        return json.loads(
            chunks.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_int=_json_integer,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError(f"{where} is not valid bounded UTF-8 JSON") from exc


def _safe_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InputError(f"{where} must be a safe lowercase identifier")
    return value


def _number(value: Any, where: str, *, allow_negative: bool = False, max_chars: int = 64) -> None:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value) or len(value) > max_chars:
        raise InputError(f"{where} must be a decimal string of at most {max_chars} characters")
    if not allow_negative and value.startswith("-"):
        raise InputError(f"{where} must be non-negative")


def _validate_g2(summary: Any) -> date:
    root = _object(summary, "g2 summary", {"schema_version", "as_of_date", "products"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("g2 summary schema_version must be integer 1")
    as_of = _date(root["as_of_date"], "g2 summary.as_of_date")
    products = root["products"]
    if type(products) is not list or not 1 <= len(products) <= 100:
        raise InputError("g2 summary.products must contain 1..100 products")
    seen: set[str] = set()
    for index, raw in enumerate(products):
        where = f"g2 summary.products[{index}]"
        product = _object(raw, where, {"product_id", "launch_date", "currency", "checkpoints"})
        product_id = _safe_id(product["product_id"], f"{where}.product_id")
        if product_id in seen:
            raise InputError("g2 summary product_id values must be unique")
        seen.add(product_id)
        launch_date = _date(product["launch_date"], f"{where}.launch_date")
        if not isinstance(product["currency"], str) or not re.fullmatch(r"[A-Z]{3}", product["currency"]):
            raise InputError(f"{where}.currency must be a three-letter uppercase code")
        checkpoints = product["checkpoints"]
        if type(checkpoints) is not list or len(checkpoints) != 2:
            raise InputError(f"{where}.checkpoints must contain two summaries")
        days: set[int] = set()
        for cp_index, raw_cp in enumerate(checkpoints):
            cp_where = f"{where}.checkpoints[{cp_index}]"
            cp = _object(raw_cp, cp_where, {"day", "due_date", "observed_on", "evidence_ref", "metrics"})
            if type(cp["day"]) is not int or cp["day"] not in {60, 90} or cp["day"] in days:
                raise InputError(f"{cp_where}.day must uniquely be 60 and 90")
            days.add(cp["day"])
            due = _date(cp["due_date"], f"{cp_where}.due_date")
            try:
                expected_due = launch_date + timedelta(days=cp["day"])
            except OverflowError as exc:
                raise InputError(f"{where}.launch_date is outside the supported checkpoint range") from exc
            if due != expected_due:
                raise InputError(f"{cp_where}.due_date does not match launch date plus checkpoint day")
            if cp["observed_on"] is not None:
                observed_on = _date(cp["observed_on"], f"{cp_where}.observed_on")
                if observed_on < due or observed_on > as_of:
                    raise InputError(f"{cp_where}.observed_on must be from its due date through g2 as_of_date")
            if cp["evidence_ref"] is not None and (
                not isinstance(cp["evidence_ref"], str) or not _EVIDENCE.fullmatch(cp["evidence_ref"])
            ):
                raise InputError(f"{cp_where}.evidence_ref must be an opaque label")
            has_observed = False
            metrics = cp["metrics"]
            if type(metrics) is not dict or not 1 <= len(metrics) <= 50:
                raise InputError(f"{cp_where}.metrics must contain 1..50 metrics")
            for metric_id, raw_metric in metrics.items():
                _safe_id(metric_id, f"{cp_where}.metric_id")
                metric_where = f"{cp_where}.metrics.{metric_id}"
                metric = _object(raw_metric, metric_where, {"status", "observed", "direction", "unit", "threshold"})
                if not isinstance(metric["status"], str) or metric["status"] not in {"met", "missed", "unmeasured"}:
                    raise InputError(f"{metric_where}.status is invalid")
                if not isinstance(metric["direction"], str) or metric["direction"] not in {"gte", "lte"}:
                    raise InputError(f"{metric_where}.direction is invalid")
                if not isinstance(metric["unit"], str) or not re.fullmatch(r"[a-z][a-z0-9_/-]{0,31}", metric["unit"]):
                    raise InputError(f"{metric_where}.unit is invalid")
                _number(metric["threshold"], f"{metric_where}.threshold")
                if metric["observed"] is not None:
                    _number(metric["observed"], f"{metric_where}.observed", allow_negative=True)
                if (metric["status"] == "unmeasured") != (metric["observed"] is None):
                    raise InputError(f"{metric_where} status and observed value disagree")
                if metric["observed"] is not None:
                    has_observed = True
                    observed, threshold = Decimal(metric["observed"]), Decimal(metric["threshold"])
                    met = observed >= threshold if metric["direction"] == "gte" else observed <= threshold
                    if (metric["status"] == "met") != met:
                        raise InputError(f"{metric_where} status does not match its explicit threshold")
            if has_observed and (cp["observed_on"] is None or cp["evidence_ref"] is None):
                raise InputError(f"{cp_where} observation dates/evidence do not match its metrics")
            if not has_observed and (cp["observed_on"] is not None or cp["evidence_ref"] is not None):
                raise InputError(f"{cp_where} cannot claim observation dates/evidence without measured metrics")
        if days != {60, 90}:
            raise InputError(f"{where}.checkpoints must contain day 60 and day 90")
    return as_of


def _validate_g3(summary: Any) -> date:
    root = _object(
        summary,
        "g3 summary",
        {"schema_version", "trend_status", "four_week_total_minutes", "four_week_change_minutes", "weeks"},
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("g3 summary schema_version must be integer 1")
    weeks = root["weeks"]
    if type(weeks) is not list or not 1 <= len(weeks) <= 104:
        raise InputError("g3 summary.weeks must contain 1..104 weeks")
    if not isinstance(root["trend_status"], str) or root["trend_status"] not in {
        "four_consecutive_weeks",
        "insufficient_consecutive_weeks",
    }:
        raise InputError("g3 summary.trend_status is invalid")
    for key in ("four_week_total_minutes", "four_week_change_minutes"):
        if root[key] is not None and type(root[key]) is not int:
            raise InputError(f"g3 summary.{key} must be an integer or null")
    if root["four_week_total_minutes"] is not None and not 0 <= root["four_week_total_minutes"] <= 4 * 10080:
        raise InputError("g3 summary.four_week_total_minutes is outside one four-week window")
    if root["four_week_change_minutes"] is not None and not -10080 <= root["four_week_change_minutes"] <= 10080:
        raise InputError("g3 summary.four_week_change_minutes is outside a weekly range")
    if (root["trend_status"] == "four_consecutive_weeks") != (root["four_week_total_minutes"] is not None):
        raise InputError("g3 summary trend status and aggregate disagree")
    starts: list[date] = []
    week_totals: list[int] = []
    for index, raw in enumerate(weeks):
        where = f"g3 summary.weeks[{index}]"
        week = _object(
            raw,
            where,
            {
                "week_start",
                "approval_minutes",
                "outage_response_minutes",
                "manual_fulfilment_minutes",
                "total_minutes",
                "change_vs_previous_week_minutes",
            },
        )
        start = _date(week["week_start"], f"{where}.week_start")
        if start.weekday() != 0 or (starts and start <= starts[-1]):
            raise InputError(f"{where}.week_start values must be strictly increasing Mondays")
        starts.append(start)
        counters = [week[key] for key in ("approval_minutes", "outage_response_minutes", "manual_fulfilment_minutes")]
        if any(type(value) is not int or not 0 <= value <= 10080 for value in counters):
            raise InputError(f"{where} minute counters must be integers from 0 to 10080")
        if type(week["total_minutes"]) is not int or week["total_minutes"] != sum(counters):
            raise InputError(f"{where}.total_minutes does not match its counters")
        if week["total_minutes"] > 10080:
            raise InputError(f"{where}.total_minutes exceeds one week")
        week_totals.append(week["total_minutes"])
        change = week["change_vs_previous_week_minutes"]
        if change is not None and type(change) is not int:
            raise InputError(f"{where}.change_vs_previous_week_minutes must be an integer or null")
        has_prior_week = index > 0 and (start - starts[index - 1]).days == 7
        expected_change = week["total_minutes"] - week_totals[index - 1] if has_prior_week else None
        if change != expected_change:
            raise InputError(f"{where}.change_vs_previous_week_minutes does not match the prior week")
    tail = weeks[-4:]
    has_consecutive_four = len(tail) == 4 and all(
        (starts[-4 + index] - starts[-5 + index]).days == 7 for index in range(1, 4)
    )
    if (root["trend_status"] == "four_consecutive_weeks") != has_consecutive_four:
        raise InputError("g3 summary trend status does not match its last four week dates")
    if has_consecutive_four:
        totals = [week["total_minutes"] for week in tail]
        if root["four_week_total_minutes"] != sum(totals):
            raise InputError("g3 summary four-week total does not match its week rows")
        if root["four_week_change_minutes"] != totals[-1] - totals[0]:
            raise InputError("g3 summary four-week change does not match its week rows")
    elif root["four_week_change_minutes"] is not None:
        raise InputError("g3 summary change must be null without four consecutive weeks")
    return starts[-1]


def _validate_g4(summary: Any) -> date:
    root = _object(
        summary,
        "g4 summary",
        {
            "schema_version",
            "period",
            "currency",
            "entities",
            "invoice_count",
            "unallocated_amount",
            "total_budget",
            "total_invoice_amount",
            "total_variance",
        },
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("g4 summary schema_version must be integer 1")
    period = root["period"]
    if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
        raise InputError("g4 summary.period must be canonical YYYY-MM")
    if not isinstance(root["currency"], str) or not re.fullmatch(r"[A-Z]{3}", root["currency"]):
        raise InputError("g4 summary.currency must be a three-letter uppercase code")
    entities = root["entities"]
    if type(entities) is not list or not 1 <= len(entities) <= 50:
        raise InputError("g4 summary.entities must contain 1..50 entities")
    seen: set[str] = set()
    for index, raw in enumerate(entities):
        where = f"g4 summary.entities[{index}]"
        entity = _object(
            raw, where, {"entity_id", "budget_amount", "invoice_total", "variance_amount", "budget_position"}
        )
        entity_id = _safe_id(entity["entity_id"], f"{where}.entity_id")
        if entity_id in seen:
            raise InputError("g4 summary entity IDs must be unique")
        seen.add(entity_id)
        for key in ("budget_amount", "invoice_total", "variance_amount"):
            _number(
                entity[key],
                f"{where}.{key}",
                allow_negative=(key == "variance_amount"),
                max_chars=70 if key != "budget_amount" else 64,
            )
        if not isinstance(entity["budget_position"], str) or entity["budget_position"] not in {
            "within_budget",
            "over_budget",
        }:
            raise InputError(f"{where}.budget_position is invalid")
        with localcontext() as context:
            context.prec = 128
            variance = Decimal(entity["budget_amount"]) - Decimal(entity["invoice_total"])
        if variance != Decimal(entity["variance_amount"]):
            raise InputError(f"{where}.variance_amount does not match its budget and invoice total")
        expected_position = "within_budget" if variance >= 0 else "over_budget"
        if entity["budget_position"] != expected_position:
            raise InputError(f"{where}.budget_position does not match its variance")
    if type(root["invoice_count"]) is not int or not 0 <= root["invoice_count"] <= 2000:
        raise InputError("g4 summary.invoice_count must be an integer from 0 to 2000")
    _number(root["unallocated_amount"], "g4 summary.unallocated_amount")
    for key in ("total_budget", "total_invoice_amount"):
        _number(root[key], f"g4 summary.{key}", max_chars=70)
    _number(root["total_variance"], "g4 summary.total_variance", allow_negative=True, max_chars=70)
    with localcontext() as context:
        context.prec = 128
        budget_total = sum((Decimal(row["budget_amount"]) for row in entities), Decimal(0))
        invoice_total = sum((Decimal(row["invoice_total"]) for row in entities), Decimal(0))
        reported_budget = Decimal(root["total_budget"])
        reported_invoices = Decimal(root["total_invoice_amount"])
        reported_variance = Decimal(root["total_variance"])
        aggregate_variance = reported_budget - reported_invoices
    if reported_budget != budget_total:
        raise InputError("g4 summary.total_budget does not match entity budgets")
    if reported_invoices != invoice_total:
        raise InputError("g4 summary.total_invoice_amount does not match entity invoice totals")
    if reported_variance != aggregate_variance:
        raise InputError("g4 summary.total_variance does not match aggregate totals")
    if Decimal(root["unallocated_amount"]) != 0:
        raise InputError("g4 summary.unallocated_amount must be zero in a valid G4 summary")
    if (root["invoice_count"] == 0) != (reported_invoices == 0):
        raise InputError("g4 summary invoice_count does not match its invoice total")
    try:
        return date.fromisoformat(f"{period}-01")
    except ValueError as exc:
        raise InputError("g4 summary.period must be a valid calendar month") from exc


def _manifest(payload: Any) -> tuple[date, dict[str, Any]]:
    root = _object(payload, "manifest", {"schema_version", "as_of_date", "components"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("manifest.schema_version must be integer 1")
    as_of = _date(root["as_of_date"], "manifest.as_of_date")
    components = root["components"]
    if type(components) is not dict or set(components) - set(_MAX_AGE_DAYS):
        raise InputError("manifest.components may contain only g2, g3, and g4")
    if any(value is None for value in components.values()):
        raise InputError("omit unavailable components instead of setting them to null")
    for name, raw in components.items():
        component = _object(
            raw, f"manifest.components.{name}", {"path", "max_age_days", "evidence_status", "provenance_note"}
        )
        if not isinstance(component["path"], str) or not component["path"] or "\x00" in component["path"]:
            raise InputError(f"manifest.components.{name}.path must be an explicit path")
        summary_path = Path(component["path"])
        if summary_path.is_absolute() or len(summary_path.parts) != 1 or summary_path.name in {".", ".."}:
            raise InputError(f"manifest.components.{name}.path must be a sibling summary filename")
        if summary_path.suffix.lower() != ".json":
            raise InputError(f"manifest.components.{name}.path must name a .json summary")
        age = component["max_age_days"]
        if type(age) is not int or not 0 <= age <= _MAX_AGE_DAYS[name]:
            raise InputError(f"manifest.components.{name}.max_age_days must be 0..{_MAX_AGE_DAYS[name]}")
        if not isinstance(component["evidence_status"], str) or component["evidence_status"] not in {
            "unverified",
            "founder_attested",
        }:
            raise InputError(f"manifest.components.{name}.evidence_status must be unverified or founder_attested")
        note = component["provenance_note"]
        if not isinstance(note, str) or note not in _PROVENANCE_NOTES:
            raise InputError(f"manifest.components.{name}.provenance_note must be an approved label")
    return as_of, components


def _g4_freshness(period_start: date, as_of: date, max_age_days: int) -> tuple[str, int | None]:
    """Classify a monthly G4 reconciliation relative to the packet date.

    A month cannot be reconciled until it closes, so age is measured from the day after the period
    ends. The latest closed month is always ``fresh``; the still-open current month is ``partial``.
    """
    period, current = (period_start.year, period_start.month), (as_of.year, as_of.month)
    if period > current:
        return "future", None
    if period == current:
        return "partial", None
    closes_on = date(period_start.year + period_start.month // 12, period_start.month % 12 + 1, 1)
    age_days = (as_of - closes_on).days
    latest_closed = (closes_on.year, closes_on.month) == current
    return ("fresh" if latest_closed or age_days <= max_age_days else "stale"), age_days


def build_packet(payload: Any, *, base_dir: Path) -> dict[str, Any]:
    """Build a descriptive packet; every supplied summary remains caller-provided and untrusted."""
    as_of, components = _manifest(payload)
    inputs: dict[str, Any] = {}
    validators = {"g2": _validate_g2, "g3": _validate_g3, "g4": _validate_g4}
    for name in ("g2", "g3", "g4"):
        component = components.get(name)
        if component is None:
            inputs[name] = {"freshness_status": "missing", "trust_status": "not_assessable", "summary": None}
            continue
        path_value = Path(component["path"])
        path = path_value if path_value.is_absolute() else base_dir / path_value
        summary = _read_json(path, f"{name} summary")
        reference_date = validators[name](summary)
        if name == "g4":
            freshness, age_days = _g4_freshness(reference_date, as_of, component["max_age_days"])
        else:
            age_days = (as_of - reference_date).days
            freshness = "future" if age_days < 0 else "stale" if age_days > component["max_age_days"] else "fresh"
        inputs[name] = {
            "freshness_status": freshness,
            "age_days": age_days,
            "max_age_days": component["max_age_days"],
            "trust_status": "untrusted",
            "caller_evidence_status": component["evidence_status"],
            "provenance_note": component["provenance_note"],
            "summary": summary,
        }
    return {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "packet_status": "review_required",
        "g1_four_week_execution_status": "not_established",
        "founder_rhythm": _RHYTHM,
        "inputs": inputs,
        "disclaimer": (
            "Summaries are caller-supplied and untrusted. This packet does not establish a G1 week, "
            "authenticate evidence, infer priorities, recommend decisions, or execute actions."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="explicit JSON manifest of sanitized G2/G3/G4 summaries")
    args = parser.parse_args(argv)
    try:
        manifest = _read_json(args.manifest, "manifest")
        packet = build_packet(manifest, base_dir=args.manifest.parent)
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(packet, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
