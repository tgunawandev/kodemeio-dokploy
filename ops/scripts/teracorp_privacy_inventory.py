#!/usr/bin/env python3
"""Check the structure of a local Teracorp privacy inventory; never assess compliance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

MAX_BYTES = 1_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_INTEGER_DIGITS = 20
ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PRODUCT_ID = re.compile(r"^prod_[a-f0-9]{32}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
CATEGORIES = {
    "account_profile",
    "contact",
    "child_data",
    "payment_metadata",
    "support_message",
    "usage_telemetry",
    "content_submission",
    "other",
}


class InputError(ValueError):
    """Input cannot safely be treated as a structural inventory."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise InputError(f"{where} has an invalid object shape")
    return value


def _bounded_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise InputError("json_integer_out_of_range")
    try:
        return int(token)
    except ValueError as exc:
        raise InputError("json_integer_out_of_range") from exc


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise InputError(f"{where} must be an opaque lowercase identifier")
    return value


def _product_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not PRODUCT_ID.fullmatch(value):
        raise InputError(f"{where} must be an opaque generated product identifier")
    return value


def _day(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise InputError(f"{where} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{where} must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise InputError(f"{where} must be canonical YYYY-MM-DD")
    return parsed


def _ids(value: Any, where: str, *, required: bool = False, maximum: int = 32) -> list[str]:
    if type(value) is not list or len(value) > maximum or (required and not value):
        raise InputError(f"{where} must be a bounded {'non-empty ' if required else ''}array")
    result = [_id(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise InputError(f"{where} contains duplicate identifiers")
    return result


def validate(payload: Any, as_of: str) -> dict[str, Any]:
    """Validate shape/references and return aggregate unverified status only."""
    cutoff = _day(as_of, "as_of")
    root = _object(
        payload,
        "inventory",
        {
            "schema_version",
            "as_of_date",
            "processor_inventory_status",
            "processors",
            "evidence_refs",
            "purpose_registry",
            "products",
        },
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("schema_version must be integer 1")
    if _day(root["as_of_date"], "as_of_date") != cutoff:
        raise InputError("as_of must match package as_of_date")
    processor_state = root["processor_inventory_status"]
    if not isinstance(processor_state, str) or processor_state not in {"complete", "none_declared", "unknown"}:
        raise InputError("processor_inventory_status is invalid")
    processors_in, evidence_in = root["processors"], root["evidence_refs"]
    purpose_registry, products_in = root["purpose_registry"], root["products"]
    if type(processors_in) is not list or len(processors_in) > 128:
        raise InputError("processors must be a bounded array")
    if type(evidence_in) is not list or len(evidence_in) > 1024:
        raise InputError("evidence_refs must be a bounded array")
    if type(purpose_registry) is not list or len(purpose_registry) > 2048:
        raise InputError("purpose_registry must be a bounded array")
    if type(products_in) is not list or not 1 <= len(products_in) <= 128:
        raise InputError("products must contain 1..128 products")
    processors: dict[str, str] = {}
    evidence: dict[str, date] = {}
    evidence_product_scope: dict[str, str | None] = {}
    linked_refs = 0
    for index, raw in enumerate(processors_in):
        where = f"processors[{index}]"
        item = _object(raw, where, {"processor_id", "inventory_status", "evidence_refs"})
        key = _id(item["processor_id"], f"{where}.processor_id")
        status = item["inventory_status"]
        if not isinstance(status, str) or status not in {"identified", "unknown"} or key in processors:
            raise InputError(f"{where} has invalid or duplicate processor identity/status")
        _ids(
            item["evidence_refs"],
            f"{where}.evidence_refs",
            required=status == "identified",
            maximum=32,
        )
        processors[key] = status
    if (processor_state == "none_declared") != (len(processors) == 0):
        raise InputError("none_declared must correspond exactly to an empty processor inventory")
    if processor_state == "complete" and (not processors or "unknown" in processors.values()):
        raise InputError("complete processor inventory must contain identified entries")
    if processor_state == "unknown" and not processors:
        pass  # Unknown is intentionally distinct from an asserted empty inventory.
    for index, raw in enumerate(evidence_in):
        where = f"evidence_refs[{index}]"
        item = _object(raw, where, {"evidence_ref_id", "sha256", "observed_on", "product_id"})
        key = _id(item["evidence_ref_id"], f"{where}.evidence_ref_id")
        if key in evidence or not isinstance(item["sha256"], str) or not SHA256.fullmatch(item["sha256"]):
            raise InputError(f"{where} has duplicate identity or invalid digest")
        observed = _day(item["observed_on"], f"{where}.observed_on")
        if observed > cutoff:
            raise InputError(f"{where} is after as_of_date")
        scope_product = item["product_id"]
        if scope_product is not None:
            scope_product = _product_id(scope_product, f"{where}.product_id")
        evidence[key] = observed
        evidence_product_scope[key] = scope_product
    for index, item in enumerate(processors_in):
        refs = set(item["evidence_refs"])
        linked_refs += len(refs)
        if not refs <= evidence.keys():
            raise InputError(f"processors[{index}] references missing evidence")
        if any(evidence_product_scope[ref] is not None for ref in refs):
            raise InputError(f"processors[{index}] requires package-scoped evidence")

    purposes_by_product: dict[str, set[str]] = {}
    for index, raw in enumerate(purpose_registry):
        where = f"purpose_registry[{index}]"
        item = _object(raw, where, {"product_id", "purpose_id"})
        product_id = _product_id(item["product_id"], f"{where}.product_id")
        purpose_id = _id(item["purpose_id"], f"{where}.purpose_id")
        purposes = purposes_by_product.setdefault(product_id, set())
        if purpose_id in purposes:
            raise InputError("purpose_id values must be unique within their product scope")
        purposes.add(purpose_id)

    product_ids: set[str] = set()
    flow_ids: set[str] = set()
    unresolved: set[str] = set()
    for index, raw in enumerate(products_in):
        where = f"products[{index}]"
        keys = {
            "product_id",
            "inventory_status",
            "consent_status",
            "consent_evidence_refs",
            "retention_status",
            "retention_evidence_refs",
            "deletion_test_status",
            "deletion_tested_on",
            "deletion_evidence_refs",
            "pia_status",
            "pia_evidence_refs",
            "data_flows",
        }
        item = _object(raw, where, keys)
        product_id = _product_id(item["product_id"], f"{where}.product_id")
        if product_id in product_ids:
            raise InputError("product_id values must be unique")
        product_ids.add(product_id)
        enums = {
            "inventory_status": {"complete", "unknown"},
            "consent_status": {"documented", "not_applicable_asserted", "unresolved"},
            "retention_status": {"documented", "unresolved"},
            "deletion_test_status": {"passed", "failed", "not_run", "unknown"},
            "pia_status": {"completed", "pending", "not_required_asserted", "unknown"},
        }
        for field, options in enums.items():
            if not isinstance(item[field], str) or item[field] not in options:
                raise InputError(f"{where}.{field} is invalid")
            if item[field] in {"unknown", "unresolved", "pending", "failed", "not_run"}:
                unresolved.add(f"products[{index}]:{field}:{item[field]}")
        refs_by_field = {
            "consent_evidence_refs": item["consent_evidence_refs"],
            "retention_evidence_refs": item["retention_evidence_refs"],
            "deletion_evidence_refs": item["deletion_evidence_refs"],
            "pia_evidence_refs": item["pia_evidence_refs"],
        }
        for field, ref_value in refs_by_field.items():
            refs = _ids(ref_value, f"{where}.{field}")
            linked_refs += len(refs)
            if not set(refs) <= evidence.keys():
                raise InputError(f"{where}.{field} references missing evidence")
            if any(evidence_product_scope[ref] != product_id for ref in refs):
                raise InputError(f"{where}.{field} references evidence outside this product scope")
        for status_field, ref_field in (
            ("consent_status", "consent_evidence_refs"),
            ("retention_status", "retention_evidence_refs"),
            ("pia_status", "pia_evidence_refs"),
        ):
            status = item[status_field]
            if status in {"documented", "completed"} and not item[ref_field]:
                raise InputError(f"{where}.{status_field} requires evidence references")
        deletion_status = item["deletion_test_status"]
        tested_on = item["deletion_tested_on"]
        deletion_refs = item["deletion_evidence_refs"]
        if deletion_status in {"passed", "failed"}:
            if tested_on is None or not deletion_refs:
                raise InputError(f"{where} executed deletion test requires date and evidence")
            deletion_date = _day(tested_on, f"{where}.deletion_tested_on")
            if deletion_date > cutoff:
                raise InputError(f"{where}.deletion_tested_on is after as_of_date")
            if any(evidence[ref] < deletion_date for ref in deletion_refs):
                raise InputError(f"{where} deletion evidence predates the recorded test")
        elif tested_on is not None or deletion_refs:
            raise InputError(f"{where} unpassed deletion test must not carry pass evidence/date")
        flows = item["data_flows"]
        if type(flows) is not list or len(flows) > 512:
            raise InputError(f"{where}.data_flows must be a bounded array")
        if item["inventory_status"] == "complete" and not flows:
            raise InputError(f"{where} complete inventory requires data flow(s)")
        for flow_index, flow_raw in enumerate(flows):
            flow_where = f"{where}.data_flows[{flow_index}]"
            flow = _object(
                flow_raw, flow_where, {"flow_id", "processor_id", "purpose_id", "data_categories", "evidence_refs"}
            )
            flow_id = _id(flow["flow_id"], f"{flow_where}.flow_id")
            if flow_id in flow_ids:
                raise InputError("flow_id values must be unique across the package")
            flow_ids.add(flow_id)
            processor_id = _id(flow["processor_id"], f"{flow_where}.processor_id")
            if processor_id not in processors:
                raise InputError(f"{flow_where} references an unknown processor")
            if processors[processor_id] == "unknown":
                unresolved.add(f"{flow_where}:processor:unknown")
            purpose_id = _id(flow["purpose_id"], f"{flow_where}.purpose_id")
            if purpose_id not in purposes_by_product.get(product_id, set()):
                raise InputError(f"{flow_where} references an unknown or cross-product purpose")
            categories = flow["data_categories"]
            if (
                type(categories) is not list
                or not 1 <= len(categories) <= 16
                or any(not isinstance(category, str) for category in categories)
                or len(set(categories)) != len(categories)
            ):
                raise InputError(f"{flow_where}.data_categories must be a non-empty unique bounded list")
            if any(not isinstance(category, str) or category not in CATEGORIES for category in categories):
                raise InputError(f"{flow_where}.data_categories contains an unknown category")
            refs = _ids(flow["evidence_refs"], f"{flow_where}.evidence_refs", required=True)
            linked_refs += len(refs)
            if not set(refs) <= evidence.keys():
                raise InputError(f"{flow_where}.evidence_refs references missing evidence")
            if any(evidence_product_scope[ref] != product_id for ref in refs):
                raise InputError(f"{flow_where}.evidence_refs references evidence outside this product scope")
        if item["inventory_status"] == "unknown":
            unresolved.add(f"{where}:inventory_status:unknown")
        if item["consent_status"] == "not_applicable_asserted":
            unresolved.add(f"{where}:consent_status:founder_asserted_unverified")
        if item["pia_status"] == "not_required_asserted":
            unresolved.add(f"{where}:pia_status:founder_asserted_unverified")
    if not set(purposes_by_product) <= product_ids:
        raise InputError("purpose_registry references an unknown product")
    if not {scope for scope in evidence_product_scope.values() if scope is not None} <= product_ids:
        raise InputError("evidence scope references an unknown product")
    if processor_state == "unknown":
        unresolved.add("processor_inventory_status:unknown")
    if processor_state == "none_declared":
        unresolved.add("processor_inventory_status:none_declared_unverified")
    return {
        "status": "inventory-structured-for-founder-counsel-review-unverified",
        "as_of_date": cutoff.isoformat(),
        "verified": False,
        "legal_reviewed": False,
        "deletion_verified": False,
        "product_count": len(product_ids),
        "processor_count": len(processors),
        "evidence_reference_count": len(evidence),
        "linked_evidence_reference_count": linked_refs,
        "unresolved_count": len(unresolved),
        "unresolved": sorted(unresolved),
    }


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


def _check_json_depth(text: str) -> None:
    stack: list[str] = []
    closing_for = {"]": "[", "}": "{"}
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
            stack.append(char)
            if len(stack) > MAX_JSON_DEPTH:
                raise InputError("input_too_deep")
        elif char in closing_for and stack and stack[-1] == closing_for[char]:
            stack.pop()


def load(path: Path) -> Any:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc
    if len(raw) > MAX_BYTES:
        raise InputError("input_too_large")
    try:
        text = raw.decode("utf-8")
        _check_json_depth(text)
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_int=_bounded_json_integer,
        )
    except InputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="explicit YYYY-MM-DD evaluation date")
    parser.add_argument("input", type=Path, help="explicit local JSON inventory")
    args = parser.parse_args(argv)
    try:
        report = validate(load(args.input), args.as_of)
    except InputError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc), "verified": False, "legal_reviewed": False}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
