#!/usr/bin/env python3
"""Offline validator and deterministic synthetic affiliate evidence reconciler."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import sys
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "affiliate.v1.schema.json"
MAX_INPUT_BYTES = 1_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_INTEGER_DIGITS = 16
MAX_AMOUNT_MINOR = 1_000_000_000_000
MAX_TOTAL_MINOR = 1_000_000_000_000
PLACEHOLDER_PREFIXES = ("todo", "replace", "tbd")


class InputError(ValueError):
    """Invalid input; error messages never echo untrusted data."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


def _bounded_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise InputError("json_integer_out_of_range")
    return int(token)


def _validate_json_depth(text: str) -> None:
    """Bound structural nesting before parsing, ignoring delimiters in strings."""
    stack: list[str] = []
    in_string = False
    escaped = False
    closing_for = {"}": "{", "]": "["}
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
            stack.append(char)
            if len(stack) > MAX_JSON_DEPTH:
                raise InputError("input_too_deep")
        elif char in closing_for and stack and stack[-1] == closing_for[char]:
            stack.pop()


def load_document(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        text = path.read_text(encoding="utf-8")
        _validate_json_depth(text)
        return json.loads(text, object_pairs_hook=_unique_object, parse_int=_bounded_json_integer)
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        Draft202012Validator.check_schema(schema)
        return schema
    except (OSError, UnicodeError, json.JSONDecodeError, InputError, ValueError) as exc:
        raise InputError("validator_schema_unavailable") from exc


def attribution_digest(attribution: dict[str, Any]) -> str:
    canonical = json.dumps(attribution, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _placeholder(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip() or value.strip().lower().startswith(PLACEHOLDER_PREFIXES)


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _destination_is_allowed(url: Any, allowlist: Any) -> bool:
    if not isinstance(url, str) or not isinstance(allowlist, list):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or port not in (None, 443)
            or parts.query
            or parts.fragment
        ):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return host.lower() in {entry.lower() for entry in allowlist if isinstance(entry, str)}
    except ValueError:
        return False


def _semantic_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    as_of = _date(payload.get("as_of"))
    advertiser = payload.get("advertiser", {})
    approval = advertiser.get("approval", {})
    terms = advertiser.get("commission_terms", {})
    if approval.get("status") != "approved" or _placeholder(approval.get("approved_by_role")):
        issues.append("advertiser.approval:founder_approval_required")
    approval_start = _date(approval.get("approved_on"))
    approval_end = _date(approval.get("valid_until"))
    if not as_of or not approval_start or not approval_end or approval_start > as_of or approval_end < as_of:
        issues.append("advertiser.approval:expired_or_invalid_window")
    if terms.get("status") != "approved":
        issues.append("advertiser.commission_terms:approved_terms_required")
    terms_start = _date(terms.get("valid_from"))
    terms_end = _date(terms.get("valid_until"))
    if not as_of or not terms_start or not terms_end or terms_start > as_of or terms_end < as_of:
        issues.append("advertiser.commission_terms:expired_or_invalid_window")
    if not isinstance(advertiser.get("display_name"), str) or _placeholder(advertiser.get("display_name")):
        issues.append("advertiser:approved_advertiser_required")

    links = payload.get("links", [])
    ids = [item.get("link_id") for item in links if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        issues.append("links:duplicate_identifiers")
    by_link = {item.get("link_id"): item for item in links if isinstance(item, dict)}
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            continue
        if not _destination_is_allowed(link.get("destination_url"), payload.get("destination_allowlist")):
            issues.append(f"links[{index}]:destination_not_allowlisted")
        disclosure = link.get("disclosure")
        if _placeholder(disclosure):
            issues.append(f"links[{index}]:disclosure_required")
        if link.get("status") != "active":
            issues.append(f"links[{index}]:link_not_active")
        expiry = _date(link.get("expires_on"))
        if not as_of or not expiry or expiry < as_of:
            issues.append(f"links[{index}]:expired_or_invalid")
        attribution = link.get("attribution")
        if not isinstance(attribution, dict) or attribution_digest(attribution) != link.get("attribution_sha256"):
            issues.append(f"links[{index}]:attribution_hash_mismatch")

    events = payload.get("events", [])
    event_ids = [item.get("event_id") for item in events if isinstance(item, dict)]
    if len(event_ids) != len(set(event_ids)):
        # Idempotent duplicate records are permitted only when byte-equivalent; checked by reconcile().
        try:
            _deduplicated_events(events)
        except InputError:
            issues.append("events:duplicate_event_conflict")
    unique_events: list[dict[str, Any]] = []
    with suppress(InputError):
        unique_events = _deduplicated_events(events)
    event_map = {event.get("event_id"): event for event in unique_events}
    for index, event in enumerate(unique_events):
        if event.get("synthetic") is not True:
            issues.append(f"events[{index}]:synthetic_only")
        when = event.get("occurred_at")
        try:
            parsed_time = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            parsed_time = None
        if not as_of or not parsed_time or parsed_time.tzinfo is None or parsed_time.date() > as_of:
            issues.append(f"events[{index}]:invalid_or_future_timestamp")
        event_date = parsed_time.date() if parsed_time and parsed_time.tzinfo else None
        if event.get("event_type") == "click":
            link = by_link.get(event.get("link_id"))
            if not link:
                issues.append(f"events[{index}]:link_missing")
            else:
                if event.get("attribution") != link.get("attribution") or event.get("attribution_sha256") != link.get(
                    "attribution_sha256"
                ):
                    issues.append(f"events[{index}]:attribution_mismatch")
                link_expiry = _date(link.get("expires_on"))
                if event_date and (
                    not terms_start or event_date < terms_start or not terms_end or event_date > terms_end
                ):
                    issues.append(f"events[{index}]:outside_terms_window")
                if event_date and (not link_expiry or event_date > link_expiry):
                    issues.append(f"events[{index}]:after_link_expiry")
        elif event.get("event_type") == "commission":
            click = event_map.get(event.get("click_event_id"))
            if not click or click.get("event_type") != "click":
                issues.append(f"events[{index}]:commission_click_missing")
            else:
                try:
                    click_time = datetime.fromisoformat(click["occurred_at"].replace("Z", "+00:00"))
                except (KeyError, AttributeError, ValueError):
                    click_time = None
                if parsed_time and click_time and parsed_time < click_time:
                    issues.append(f"events[{index}]:commission_precedes_click")
            if event_date and (not terms_start or event_date < terms_start or not terms_end or event_date > terms_end):
                issues.append(f"events[{index}]:outside_terms_window")
            expected_currency = terms.get("currency")
            if event.get("currency") != expected_currency:
                issues.append(f"events[{index}]:commission_currency_mismatch")
            if isinstance(event.get("order_amount_minor"), int) and isinstance(terms.get("rate_bps"), int):
                expected = (event["order_amount_minor"] * terms["rate_bps"] + 5000) // 10000
                if event.get("commission_minor") != expected:
                    issues.append(f"events[{index}]:commission_amount_mismatch")
    commission_total = sum(
        event.get("commission_minor", 0)
        for event in unique_events
        if event.get("event_type") == "commission" and type(event.get("commission_minor")) is int
    )
    if commission_total > MAX_TOTAL_MINOR:
        issues.append("events:aggregate_commission_amount_out_of_range")
    return issues


def evaluate(payload: Any) -> dict[str, Any]:
    """Return an unverified candidate state without echoing submitted values."""
    if type(payload) is not dict:
        return {
            "status": "blocked",
            "issues": ["document:object_required"],
            "verified": False,
            "live_integration": False,
            "manual_review_required": True,
            "approval_authenticated": False,
            "consent_captured": False,
            "consent_authenticated": False,
            "as_of_authenticated": False,
        }
    try:
        validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
    except InputError:
        return {
            "status": "blocked",
            "issues": ["validator:schema_unavailable"],
            "verified": False,
            "live_integration": False,
            "manual_review_required": True,
            "approval_authenticated": False,
            "consent_captured": False,
            "consent_authenticated": False,
            "as_of_authenticated": False,
        }
    errors = sorted(
        validator.iter_errors(payload), key=lambda error: (list(map(str, error.absolute_path)), error.validator or "")
    )
    issues = [f"document:{'.'.join(map(str, error.absolute_path)) or 'root'}:invalid_shape" for error in errors]
    if not issues:
        issues = _semantic_issues(payload)
    return {
        "status": "blocked" if issues else "candidate-ready-unverified",
        "issues": issues,
        "verified": False,
        "live_integration": False,
        "manual_review_required": True,
        "approval_authenticated": False,
        "consent_captured": False,
        "consent_authenticated": False,
        "as_of_authenticated": False,
    }


def _deduplicated_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        raise InputError("events_invalid")
    seen: dict[str, str] = {}
    unique: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise InputError("event_invalid")
        fingerprint = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        event_id = event["event_id"]
        if event_id in seen:
            if seen[event_id] != fingerprint:
                raise InputError("duplicate_event_conflict")
            continue
        seen[event_id] = fingerprint
        unique.append(event)
    return unique


def reconcile(payload: Any) -> dict[str, Any]:
    """Produce idempotent synthetic click/commission totals; never initiate payment."""
    report = evaluate(payload)
    if report["status"] != "candidate-ready-unverified":
        reason = next(
            (
                issue.rsplit(":", 1)[-1]
                for issue in report["issues"]
                if issue.endswith(
                    ("duplicate_event_conflict", "commission_amount_mismatch", "commission_click_missing")
                )
            ),
            "candidate_blocked",
        )
        raise InputError(reason)
    events = payload["events"]
    unique = _deduplicated_events(events)
    click_ids = {event["event_id"] for event in unique if event["event_type"] == "click"}
    commissions = [event for event in unique if event["event_type"] == "commission"]
    if any(event.get("click_event_id") not in click_ids for event in commissions):
        raise InputError("commission_click_missing")
    digest = hashlib.sha256(
        json.dumps(unique, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return {
        "status": "synthetic-evidence-only",
        "candidate_id": payload["candidate_id"],
        "synthetic": True,
        "click_count": sum(event["event_type"] == "click" for event in unique),
        "commission_count": len(commissions),
        "commission_total_minor": sum(event["commission_minor"] for event in commissions),
        "currency": payload["advertiser"]["commission_terms"]["currency"],
        "duplicate_event_count": len(events) - len(unique),
        "source_events_sha256": digest,
        "payments_created": False,
        "verified": False,
        "manual_review_required": True,
        "approval_authenticated": False,
        "consent_captured": False,
        "consent_authenticated": False,
        "as_of_authenticated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "reconcile"))
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        document = load_document(args.input)
        result = evaluate(document) if args.command == "validate" else reconcile(document)
    except InputError as exc:
        result = {
            "status": "blocked",
            "issues": [str(exc)],
            "verified": False,
            "live_integration": False,
            "manual_review_required": True,
            "approval_authenticated": False,
            "consent_captured": False,
            "consent_authenticated": False,
            "as_of_authenticated": False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"candidate-ready-unverified", "synthetic-evidence-only"} else 1


if __name__ == "__main__":
    sys.exit(main())
