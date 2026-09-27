#!/usr/bin/env python3
"""Offline, deterministic validator for the local opportunity evidence candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "opportunity_research.v1.schema.json"
MAX_INPUT_BYTES = 262_144
MAX_JSON_DEPTH = 32
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<![A-Za-z0-9])\+?\d[\d ()-]{6,}\d(?![A-Za-z0-9])")
SECRET_RE = re.compile(
    r"\b(?:bearer\s+\S+|(?:api[ _-]?key|access[ _-]?token|password|passwd|secret|private[ _-]?key)\s*[:=])"
    r"|\bsk-[A-Za-z0-9_-]{12,}\b|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    re.IGNORECASE,
)
PII_SECRET_PATTERNS = (EMAIL_RE, URL_RE, PHONE_RE, SECRET_RE)


class InputError(ValueError):
    """Invalid input; messages do not include caller-supplied values."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


def load_document(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        raw = path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        text = raw.decode("utf-8")
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
                    raise InputError("input_too_deep")
            elif char in "]}":
                depth -= 1
        return json.loads(text, object_pairs_hook=_unique_object)
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def claim_sha256(claim: str) -> str:
    """Hash exact UTF-8 claim bytes; no trimming or Unicode normalization is implicit."""
    return hashlib.sha256(claim.encode("utf-8")).hexdigest()


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        Draft202012Validator.check_schema(schema)
        return schema
    except (OSError, UnicodeError, json.JSONDecodeError, InputError, ValueError) as exc:
        raise InputError("validator_schema_unavailable") from exc


def _canonical_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _add_safe_text_issues(text: Any, path: str, issues: list[str]) -> None:
    if not isinstance(text, str):
        return
    for pattern in PII_SECRET_PATTERNS:
        if pattern.search(text):
            issues.append(f"{path}:unsafe_text_pattern")
            return


def _semantic_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    as_of = _canonical_date(payload.get("as_of_date"))
    evidence = payload.get("evidence", [])
    opportunities = payload.get("opportunities", [])

    evidence_by_ref: dict[str, dict[str, Any]] = {}
    capture_fingerprints: set[tuple[str, str, str, str]] = set()
    conflicting_refs: set[str] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            continue
        ref = item.get("evidence_ref")
        path = f"evidence[{index}]"
        captured = item.get("captured_claim")
        if isinstance(captured, str):
            if claim_sha256(captured) != item.get("captured_claim_sha256"):
                issues.append(f"{path}.captured_claim:hash_mismatch")
            _add_safe_text_issues(captured, f"{path}.captured_claim", issues)
        source_date = _canonical_date(item.get("captured_on"))
        if as_of and source_date and source_date > as_of:
            issues.append(f"{path}.captured_on:after_as_of_date")
        if isinstance(ref, str):
            previous = evidence_by_ref.get(ref)
            if previous is not None:
                if previous == item:
                    issues.append("evidence:duplicate_reference")
                else:
                    conflicting_refs.add(ref)
            else:
                evidence_by_ref[ref] = item
        fingerprint_values = (
            item.get("source_domain"),
            item.get("captured_on"),
            item.get("source_type"),
            item.get("captured_claim_sha256"),
        )
        if all(isinstance(value, str) for value in fingerprint_values):
            fingerprint = tuple(fingerprint_values)
            if fingerprint in capture_fingerprints:
                issues.append("evidence:duplicate_capture")
            capture_fingerprints.add(fingerprint)
    if conflicting_refs:
        issues.append("evidence:conflicting_reference")

    opportunity_refs: set[str] = set()
    for index, item in enumerate(opportunities):
        if not isinstance(item, dict):
            continue
        path = f"opportunities[{index}]"
        ref = item.get("opportunity_ref")
        if isinstance(ref, str):
            if ref in opportunity_refs:
                issues.append("opportunities:duplicate_reference")
            opportunity_refs.add(ref)
        claim = item.get("claim")
        if isinstance(claim, dict):
            text = claim.get("text")
            if isinstance(text, str):
                if claim_sha256(text) != claim.get("sha256"):
                    issues.append(f"{path}.claim:hash_mismatch")
                _add_safe_text_issues(text, f"{path}.claim", issues)
        refs = item.get("evidence_refs", [])
        if isinstance(refs, list) and any(ref not in evidence_by_ref for ref in refs):
            issues.append(f"{path}.evidence_refs:unknown_reference")
        review = item.get("owner_review")
        if isinstance(review, dict):
            review_status = review.get("status")
            reviewed_on = _canonical_date(review.get("reviewed_on"))
            if review_status == "pending" and review.get("reviewed_on") is not None:
                issues.append(f"{path}.owner_review:pending_has_review_date")
            elif review_status in {"reviewed_unverified", "rejected"} and not reviewed_on:
                issues.append(f"{path}.owner_review:review_date_required")
            if as_of and reviewed_on and reviewed_on > as_of:
                issues.append(f"{path}.owner_review:after_as_of_date")
            expected_status = {
                "pending": "unreviewed",
                "reviewed_unverified": "reviewed_unverified",
                "rejected": "rejected",
            }.get(review_status)
            if expected_status != item.get("status"):
                issues.append(f"{path}.status:owner_review_mismatch")

    return sorted(set(issues))


def evaluate(payload: Any) -> dict[str, Any]:
    """Validate the shape and links without asserting that any source is true."""
    if type(payload) is not dict:
        return _report(["document:object_required"])
    try:
        validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
    except InputError:
        return _report(["validator:schema_unavailable"])
    shape_errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (list(map(str, error.absolute_path)), error.validator or ""),
    )
    if shape_errors:
        return _report(
            [f"document:{'.'.join(map(str, error.absolute_path)) or 'root'}:invalid_shape" for error in shape_errors]
        )
    issues = _semantic_issues(payload)
    if issues:
        return _report(issues)

    return {
        "status": "candidate-unverified",
        "issues": [],
        "source_assertions_verified": False,
        # owner_review is caller-supplied metadata, not an authenticated attestation.
        "manual_review_required": True,
        "owner_review_authenticated": False,
        "external_access": False,
    }


def _report(issues: list[str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "issues": sorted(set(issues)),
        "source_assertions_verified": False,
        "manual_review_required": True,
        "external_access": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate one local JSON ledger")
    validate_parser.add_argument("document", type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate(load_document(args.document))
    except InputError as exc:
        report = _report([f"input:{exc}"])
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "candidate-unverified" else 1


if __name__ == "__main__":
    sys.exit(main())
