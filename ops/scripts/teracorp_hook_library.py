#!/usr/bin/env python3
"""Validate offline Teracorp hook/performance records; never makes or applies decisions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

BRANDS = {"terakod", "terakidz", "terafin", "terakon"}
HOOK_TYPES = {"problem", "benefit", "curiosity", "social_proof", "offer", "story"}
EVIDENCE_KINDS = {"performance_source", "threshold_approval", "founder_approval"}
MAX_INPUT_BYTES = 2_000_000
MAX_JSON_DEPTH = 64
MAX_HOOKS = 200
MAX_EVIDENCE = 4_000
MAX_WINDOWS = 2_000
MAX_AGE_DAYS = 30
MAX_WINDOW_DAYS = 90
MAX_COUNT = 1_000_000_000
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RATE = re.compile(r"^(?:0(?:\.[0-9]{1,6})?|1(?:\.0{1,6})?)$")


class InputError(ValueError):
    """Input is invalid or cannot safely support the supplied explicit record."""


def _obj(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        raise InputError(f"{where} has missing or unknown keys")
    return value


def _date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise InputError(f"{where} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{where} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise InputError(f"{where} must be canonical YYYY-MM-DD")
    return parsed


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InputError(f"{where} must be a safe lowercase identifier")
    return value


def _positive_int(value: Any, where: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f"{where} must be an integer in range")
    return value


def _closed_enum(value: Any, options: set[str], where: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise InputError(f"{where} is not an allowed value")
    return value


def validate_library(payload: Any) -> dict[str, Any]:
    """Validate data, dedupe identical replays, and validate only explicit decisions."""
    root = _obj(
        payload,
        "library",
        {"schema_version", "as_of_date", "hooks", "evidence_refs", "performance_windows", "retirement_decisions"},
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("library.schema_version must be integer 1")
    as_of = _date(root["as_of_date"], "library.as_of_date")
    hooks_in = root["hooks"]
    evidence_in = root["evidence_refs"]
    windows_in = root["performance_windows"]
    decisions_in = root["retirement_decisions"]
    if type(hooks_in) is not list or not 1 <= len(hooks_in) <= MAX_HOOKS:
        raise InputError("library.hooks must contain 1..200 entries")
    if type(evidence_in) is not list or len(evidence_in) > MAX_EVIDENCE:
        raise InputError("library.evidence_refs must contain at most 4000 entries")
    if type(windows_in) is not list or len(windows_in) > MAX_WINDOWS:
        raise InputError("library.performance_windows must contain at most 2000 entries")
    if type(decisions_in) is not list or len(decisions_in) > MAX_HOOKS:
        raise InputError("library.retirement_decisions must contain at most 200 entries")

    hooks: dict[tuple[str, int], dict[str, Any]] = {}
    for i, raw in enumerate(hooks_in):
        where = f"hooks[{i}]"
        item = _obj(
            raw, where, {"hook_id", "version", "brand", "hook_type", "variant_label", "created_on", "lifecycle"}
        )
        hook_id = _id(item["hook_id"], f"{where}.hook_id")
        version = _positive_int(item["version"], f"{where}.version", 1, 2_147_483_647)
        brand = _closed_enum(item["brand"], BRANDS, f"{where}.brand")
        hook_type = _closed_enum(item["hook_type"], HOOK_TYPES, f"{where}.hook_type")
        variant = _id(item["variant_label"], f"{where}.variant_label")
        created = _date(item["created_on"], f"{where}.created_on")
        lifecycle = _closed_enum(item["lifecycle"], {"active", "retired"}, f"{where}.lifecycle")
        if created > as_of:
            raise InputError(f"{where}.created_on is in the future")
        key = hook_id, version
        if key in hooks:
            raise InputError("hook_id/version pairs must be unique")
        hooks[key] = {
            "hook_id": hook_id,
            "version": version,
            "brand": brand,
            "hook_type": hook_type,
            "variant_label": variant,
            "created_on": created,
            "lifecycle": lifecycle,
        }

    evidence: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(evidence_in):
        where = f"evidence_refs[{i}]"
        item = _obj(
            raw, where, {"evidence_ref_id", "brand", "evidence_kind", "verification_status", "sha256", "observed_on"}
        )
        ref_id = _id(item["evidence_ref_id"], f"{where}.evidence_ref_id")
        brand = _closed_enum(item["brand"], BRANDS, f"{where}.brand")
        kind = _closed_enum(item["evidence_kind"], EVIDENCE_KINDS, f"{where}.evidence_kind")
        status = _closed_enum(item["verification_status"], {"verified", "unverified"}, f"{where}.verification_status")
        digest = item["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise InputError(f"{where}.sha256 must be lowercase SHA-256 hex")
        observed = _date(item["observed_on"], f"{where}.observed_on")
        if observed > as_of:
            raise InputError(f"{where}.observed_on is in the future")
        if status != "verified":
            raise InputError("unverified evidence fails closed")
        if ref_id in evidence:
            raise InputError("evidence_ref_id values must be unique")
        evidence[ref_id] = {
            "evidence_ref_id": ref_id,
            "brand": brand,
            "evidence_kind": kind,
            "verification_status": status,
            "sha256": digest,
            "observed_on": observed,
        }

    windows: dict[str, dict[str, Any]] = {}
    replay_count = 0
    for i, raw in enumerate(windows_in):
        where = f"performance_windows[{i}]"
        item = _obj(
            raw,
            where,
            {
                "schema_version",
                "window_id",
                "hook_id",
                "hook_version",
                "brand",
                "window_start",
                "window_end",
                "observed_on",
                "provenance",
                "source_id",
                "impressions",
                "engagements",
                "conversions",
                "evidence_ref_id",
            },
        )
        if type(item["schema_version"]) is not int or item["schema_version"] != 1:
            raise InputError(f"{where}.schema_version must be integer 1")
        window_id = _id(item["window_id"], f"{where}.window_id")
        hook_id = _id(item["hook_id"], f"{where}.hook_id")
        version = _positive_int(item["hook_version"], f"{where}.hook_version", 1, 2_147_483_647)
        brand = _closed_enum(item["brand"], BRANDS, f"{where}.brand")
        hook = hooks.get((hook_id, version))
        if hook is None or hook["brand"] != brand:
            raise InputError(f"{where} does not match an existing hook brand/version")
        start = _date(item["window_start"], f"{where}.window_start")
        end = _date(item["window_end"], f"{where}.window_end")
        observed = _date(item["observed_on"], f"{where}.observed_on")
        if start < hook["created_on"]:
            raise InputError(f"{where} measurement window predates the hook version")
        if end < start or (end - start).days + 1 > MAX_WINDOW_DAYS:
            raise InputError(f"{where} date window is invalid or exceeds 90 days")
        if end > as_of or observed < end or observed > as_of:
            raise InputError(f"{where} observation dates are inconsistent with as_of_date")
        provenance = _closed_enum(item["provenance"], {"real", "synthetic"}, f"{where}.provenance")
        source_id = _id(item["source_id"], f"{where}.source_id")
        counts = {
            name: _positive_int(item[name], f"{where}.{name}", 0, MAX_COUNT)
            for name in ("impressions", "engagements", "conversions")
        }
        if counts["engagements"] > counts["impressions"] or counts["conversions"] > counts["engagements"]:
            raise InputError(f"{where} aggregate counts violate funnel ordering")
        ref_id = _id(item["evidence_ref_id"], f"{where}.evidence_ref_id")
        ref = evidence.get(ref_id)
        if ref is None or ref["brand"] != brand or ref["evidence_kind"] != "performance_source":
            raise InputError(f"{where} evidence reference is missing, wrong-kind, or cross-brand")
        if ref["observed_on"] < end:
            raise InputError(f"{where} evidence predates its measurement window")
        normalized = {
            "schema_version": 1,
            "window_id": window_id,
            "hook_id": hook_id,
            "hook_version": version,
            "brand": brand,
            "window_start": start,
            "window_end": end,
            "observed_on": observed,
            "provenance": provenance,
            "source_id": source_id,
            **counts,
            "evidence_ref_id": ref_id,
        }
        previous = windows.get(window_id)
        if previous is not None:
            if previous != normalized:
                raise InputError("conflicting window_id replay")
            replay_count += 1
            continue
        windows[window_id] = normalized

    by_hook: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for window in windows.values():
        by_hook.setdefault((window["hook_id"], window["hook_version"]), []).append(window)
    for group in by_hook.values():
        group.sort(key=lambda row: row["window_start"])
        for prior, current in pairwise(group):
            if current["window_start"] <= prior["window_end"]:
                raise InputError("overlapping distinct performance windows are not allowed")

    decisions: list[dict[str, Any]] = []
    seen_decisions: set[tuple[str, int]] = set()
    for i, raw in enumerate(decisions_in):
        where = f"retirement_decisions[{i}]"
        item = _obj(
            raw,
            where,
            {"hook_id", "version", "decision", "decided_on", "approved_by", "approval_evidence_ref", "thresholds"},
        )
        hook_id = _id(item["hook_id"], f"{where}.hook_id")
        version = _positive_int(item["version"], f"{where}.version", 1, 2_147_483_647)
        key = hook_id, version
        hook = hooks.get(key)
        if hook is None:
            raise InputError(f"{where} references an unknown hook version")
        if key in seen_decisions:
            raise InputError("only one explicit decision per hook version is allowed")
        seen_decisions.add(key)
        decision = _closed_enum(item["decision"], {"retire", "retain"}, f"{where}.decision")
        decided_on = _date(item["decided_on"], f"{where}.decided_on")
        if decided_on > as_of:
            raise InputError(f"{where}.decided_on is after as_of_date")
        approver = _id(item["approved_by"], f"{where}.approved_by")
        approval_ref_id = _id(item["approval_evidence_ref"], f"{where}.approval_evidence_ref")
        approval_ref = evidence.get(approval_ref_id)
        if (
            not approval_ref
            or approval_ref["brand"] != hook["brand"]
            or approval_ref["evidence_kind"] != "founder_approval"
        ):
            raise InputError(f"{where} requires verified same-brand founder approval evidence")
        if approval_ref["observed_on"] > decided_on:
            raise InputError(f"{where} approval evidence postdates the decision")
        thresholds = _obj(
            item["thresholds"],
            f"{where}.thresholds",
            {
                "minimum_real_windows",
                "minimum_impressions_per_window",
                "maximum_conversion_rate",
                "threshold_evidence_ref",
            },
        )
        minimum_windows = _positive_int(
            thresholds["minimum_real_windows"], f"{where}.thresholds.minimum_real_windows", 2, 12
        )
        min_impressions = _positive_int(
            thresholds["minimum_impressions_per_window"],
            f"{where}.thresholds.minimum_impressions_per_window",
            1,
            MAX_COUNT,
        )
        rate_text = thresholds["maximum_conversion_rate"]
        if not isinstance(rate_text, str) or not _RATE.fullmatch(rate_text):
            raise InputError(f"{where}.thresholds.maximum_conversion_rate must be a decimal from 0 to 1")
        try:
            max_rate = Decimal(rate_text)
        except InvalidOperation as exc:
            raise InputError(f"{where}.thresholds.maximum_conversion_rate is invalid") from exc
        threshold_ref_id = _id(thresholds["threshold_evidence_ref"], f"{where}.thresholds.threshold_evidence_ref")
        threshold_ref = evidence.get(threshold_ref_id)
        if (
            not threshold_ref
            or threshold_ref["brand"] != hook["brand"]
            or threshold_ref["evidence_kind"] != "threshold_approval"
        ):
            raise InputError(f"{where} requires verified same-brand threshold approval evidence")
        if threshold_ref["observed_on"] > decided_on:
            raise InputError(f"{where} threshold approval evidence postdates the decision")

        support = sorted(by_hook.get(key, []), key=lambda row: row["window_end"])
        fresh = [
            row
            for row in support
            if row["window_end"] <= decided_on
            and row["observed_on"] <= decided_on
            and evidence[row["evidence_ref_id"]]["observed_on"] <= decided_on
            and 0 <= (decided_on - row["window_end"]).days <= MAX_AGE_DAYS
            and (as_of - row["window_end"]).days <= MAX_AGE_DAYS
        ]
        if len(fresh) < minimum_windows:
            raise InputError(f"{where} lacks enough fresh performance windows available by decision date")
        qualifying = [
            row
            for row in fresh
            if row["provenance"] == "real"
            and row["impressions"] >= min_impressions
            and row["impressions"] > 0
            and (
                Decimal(row["conversions"]) / Decimal(row["impressions"]) <= max_rate
                if decision == "retire"
                else Decimal(row["conversions"]) / Decimal(row["impressions"]) > max_rate
            )
        ]
        if len(qualifying) < minimum_windows:
            raise InputError(f"{where} lacks enough qualifying real windows for the explicit decision")
        decisions.append(
            {
                "hook_id": hook_id,
                "version": version,
                "brand": hook["brand"],
                "decision": decision,
                "decided_on": decided_on,
                "approved_by": approver,
                "approval_evidence_ref": approval_ref_id,
                "thresholds": {
                    "minimum_real_windows": minimum_windows,
                    "minimum_impressions_per_window": min_impressions,
                    "maximum_conversion_rate": rate_text,
                    "threshold_evidence_ref": threshold_ref_id,
                },
                "qualifying_real_window_ids": sorted(row["window_id"] for row in qualifying),
            }
        )

    hook_rows = [{**row, "created_on": row["created_on"].isoformat()} for row in hooks.values()]
    hook_rows.sort(key=lambda row: (row["brand"], row["hook_id"], row["version"]))
    coverage = []
    for hook in hook_rows:
        related = by_hook.get((hook["hook_id"], hook["version"]), [])
        fresh_count = sum((as_of - row["window_end"]).days <= MAX_AGE_DAYS for row in related)
        if not related:
            state = "missing"
        elif fresh_count == 0:
            state = "stale"
        else:
            state = "fresh"
        coverage.append(
            {
                "hook_id": hook["hook_id"],
                "version": hook["version"],
                "brand": hook["brand"],
                "state": state,
                "window_count": len(related),
                "fresh_window_count": fresh_count,
                "missing_is_not_zero": True,
            }
        )
    window_rows = []
    for row in windows.values():
        out = dict(row)
        for field in ("window_start", "window_end", "observed_on"):
            out[field] = out[field].isoformat()
        out["freshness"] = "fresh" if (as_of - row["window_end"]).days <= MAX_AGE_DAYS else "stale"
        out["outcome_classification"] = (
            "synthetic_sample_not_real_outcome"
            if row["provenance"] == "synthetic"
            else "real_source_claim_unverified_by_tool"
        )
        out["conversion_rate"] = (
            str((Decimal(row["conversions"]) / Decimal(row["impressions"])).quantize(Decimal("0.000001")))
            if row["impressions"]
            else None
        )
        window_rows.append(out)
    window_rows.sort(
        key=lambda row: (row["brand"], row["hook_id"], row["hook_version"], row["window_start"], row["window_id"])
    )
    for row in decisions:
        row["decided_on"] = row["decided_on"].isoformat()
    decisions.sort(key=lambda row: (row["brand"], row["hook_id"], row["version"]))
    return {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "hooks": hook_rows,
        "coverage": coverage,
        "performance_windows": window_rows,
        "retirement_decisions": decisions,
        "identical_replays_collapsed": replay_count,
        "decision_policy": "explicit_input_only; no lifecycle mutation, publishing, posting, spending, or agent action",
    }


def _load_input(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError("cannot read input file") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise InputError("input exceeds the 2000000-byte limit")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("input must be UTF-8 JSON") from exc
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise InputError("input exceeds the 64-level JSON nesting limit")
        elif char in "]}":
            depth -= 1

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InputError("input contains a duplicate object key")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InputError("input must be UTF-8 JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="local JSON document; read only")
    args = parser.parse_args(argv)
    try:
        result = validate_library(_load_input(args.input))
    except InputError as exc:
        print(json.dumps({"error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
