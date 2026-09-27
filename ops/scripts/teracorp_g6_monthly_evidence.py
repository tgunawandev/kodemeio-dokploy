#!/usr/bin/env python3
"""Offline structural inventory check for a synthetic or owner-supplied G6 package.

This tool checks references and caller-supplied timestamps only. It does not verify
evidence, counsel identity/qualifications, inventory exhaustiveness, or legal outcomes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 262_144
MAX_JSON_DEPTH = 64
TOPICS = (
    "pdp_data_protection",
    "ojk_regulatory_boundary",
    "terakod_contract_seller_of_record",
    "processor_data_map_changes",
    "access_retention_deletion",
    "incident_exception_remediation",
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "teracorp_g6_monthly_evidence.v1.schema.json"


class InputError(ValueError):
    """Invalid input with a fixed, non-echoing message."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise InputError("invalid_json_constant")


def _enforce_json_depth(text: str) -> None:
    """Bound container nesting before parsing, ignoring bracket characters in strings."""
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
            if depth > MAX_JSON_DEPTH:
                raise InputError("json_nesting_too_deep")
        elif character in "]}" and depth:
            depth -= 1


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(schema)
        return schema
    except Exception as exc:
        raise InputError("validator_schema_unavailable") from exc


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _add(issues: set[str], path: str, reason: str) -> None:
    issues.add(f"{path}:{reason}")


def _evaluate_valid_shape(payload: dict[str, Any], as_of_value: Any, issues: set[str]) -> None:
    month = payload["month"]
    as_of = _timestamp(as_of_value)
    if as_of is None:
        _add(issues, "as_of", "timezone_timestamp_required")
        return

    decisions = payload["decisions"]
    by_id: dict[str, dict[str, Any]] = {}
    for index, decision in enumerate(decisions):
        path = f"decisions[{index}]"
        decision_id = decision["decision_id"]
        if decision_id in by_id:
            _add(issues, path, "duplicate_decision_id")
        else:
            by_id[decision_id] = decision

        issued = _timestamp(decision["issued_at"])
        valid_from = _timestamp(decision["valid_from"])
        valid_until = _timestamp(decision["valid_until"])
        if None in (issued, valid_from, valid_until):
            _add(issues, path, "timezone_timestamp_required")
            continue
        if issued > as_of or valid_from > as_of:
            _add(issues, path, "decision_future")
        if valid_until < as_of:
            _add(issues, path, "decision_expired")
        if valid_until < valid_from or issued > valid_from:
            _add(issues, path, "invalid_decision_window")
        if decision["scope_type"] == "month" and decision["scope_id"] != month:
            _add(issues, path, "month_scope_mismatch")

    for topic in TOPICS:
        candidates = [
            decision
            for decision in decisions
            if decision["topic"] == topic and decision["scope_type"] == "month" and decision["scope_id"] == month
        ]
        if not candidates:
            _add(issues, f"decisions.{topic}", "missing_month_decision")
        elif not any(decision["status"] == "decided" for decision in candidates):
            _add(issues, f"decisions.{topic}", "decision_unresolved")

    for index, decision in enumerate(decisions):
        if decision["status"] == "unresolved":
            _add(issues, f"decisions[{index}]", "decision_unresolved")

    changes = payload["change_inventory"]
    if changes["inventory_status"] != "complete":
        _add(issues, "change_inventory", "inventory_unknown")
    if changes["processor_inventory_status"] == "unknown":
        _add(issues, "change_inventory", "processor_inventory_unknown")
    items = changes["items"]
    subjects: set[tuple[str, str]] = set()
    product_count = 0
    processor_count = 0
    for index, item in enumerate(items):
        path = f"change_inventory.items[{index}]"
        subject = (item["subject_type"], item["subject_id"])
        if subject in subjects:
            _add(issues, path, "duplicate_subject")
        subjects.add(subject)
        if item["subject_type"] == "product":
            product_count += 1
        else:
            processor_count += 1
        if item["change_status"] == "unknown":
            _add(issues, path, "change_status_unknown")
        linked = by_id.get(item["decision_id"])
        if (
            linked is None
            or linked["topic"] != "processor_data_map_changes"
            or linked["scope_type"] != item["subject_type"]
            or linked["scope_id"] != item["subject_id"]
            or linked["status"] != "decided"
        ):
            _add(issues, path, "decision_uncovered")
    if product_count == 0:
        _add(issues, "change_inventory", "product_inventory_missing")
    if changes["processor_inventory_status"] == "none" and processor_count:
        _add(issues, "change_inventory", "processor_inventory_contradiction")
    if changes["processor_inventory_status"] == "complete" and processor_count == 0:
        _add(issues, "change_inventory", "processor_inventory_entries_missing")

    incidents = payload["incident_inventory"]
    if incidents["inventory_status"] != "complete":
        _add(issues, "incident_inventory", "inventory_unknown")
    incident_ids: set[str] = set()
    for index, item in enumerate(incidents["items"]):
        path = f"incident_inventory.items[{index}]"
        if item["incident_id"] in incident_ids:
            _add(issues, path, "duplicate_incident_id")
        incident_ids.add(item["incident_id"])
        if item["handling_status"] != "handled":
            _add(issues, path, "incident_unhandled")
        linked = by_id.get(item["decision_id"])
        if (
            linked is None
            or linked["topic"] != "incident_exception_remediation"
            or linked["scope_type"] != "incident"
            or linked["scope_id"] != item["incident_id"]
            or linked["status"] != "decided"
        ):
            _add(issues, path, "decision_uncovered")


def evaluate(payload: Any, as_of: Any) -> dict[str, Any]:
    """Return only a safe, deterministic evidence-inventory status."""
    issues: set[str] = set()
    if type(payload) is not dict:
        return _report(issues={"document:object_required"})
    try:
        from jsonschema import Draft202012Validator, FormatChecker

        validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: (tuple(map(str, error.absolute_path)), error.validator or ""),
        )
    except Exception:
        return _report(issues={"validator:schema_unavailable"})
    if errors:
        for error in errors:
            location = ".".join(map(str, error.absolute_path)) or "root"
            _add(issues, f"document.{location}", "invalid_shape")
    else:
        _evaluate_valid_shape(payload, as_of, issues)
    return _report(payload=payload, issues=issues)


def _report(payload: dict[str, Any] | None = None, issues: set[str] | None = None) -> dict[str, Any]:
    all_issues = sorted(issues or set())
    decisions = payload.get("decisions") if payload and isinstance(payload.get("decisions"), list) else []
    change_inventory = payload.get("change_inventory") if payload else None
    change_items = (
        change_inventory.get("items")
        if isinstance(change_inventory, dict) and isinstance(change_inventory.get("items"), list)
        else []
    )
    incident_inventory = payload.get("incident_inventory") if payload else None
    incident_items = (
        incident_inventory.get("items")
        if isinstance(incident_inventory, dict) and isinstance(incident_inventory.get("items"), list)
        else []
    )
    return {
        "status": "complete-for-counsel-review-unverified" if not all_issues else "incomplete",
        "issues": all_issues,
        "verified": False,
        "legal_reviewed": False,
        "inventory_counts": {
            "decisions": len(decisions),
            "change_items": len(change_items),
            "incident_items": len(incident_items),
        },
    }


def load_document(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        text = raw.decode("utf-8")
        _enforce_json_depth(text)
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def parse_as_of(value: str) -> str:
    if _timestamp(value) is None:
        raise argparse.ArgumentTypeError("as_of_requires_timezone_timestamp")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, type=parse_as_of, help="explicit timezone-aware timestamp")
    parser.add_argument("document", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = load_document(args.document)
    except InputError as exc:
        print(json.dumps(_report(issues={f"input:{exc}"}), sort_keys=True))
        return 1
    report = evaluate(payload, args.as_of)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "complete-for-counsel-review-unverified" else 1


if __name__ == "__main__":
    sys.exit(main())
