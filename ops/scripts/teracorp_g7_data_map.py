#!/usr/bin/env python3
"""Offline completeness check for synthetic or owner-supplied Teracorp G7 maps.

This tool checks structure and explicitly supplied fields only. It does not
verify facts, assess lawfulness, or issue compliance/legal conclusions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

MAX_INPUT_BYTES = 1_000_000
PRODUCT_IDS = {f"B{i}" for i in range(1, 13)}
SECTIONS = (
    "purposes",
    "data_categories",
    "lawful_basis",
    "consent",
    "processing_operations",
    "processors",
    "residency_transfers",
    "retention",
    "access_roles",
    "deletion_procedure",
    "deletion_test",
    "pia",
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "teracorp_g7_data_map.v1.schema.json"


class InputError(ValueError):
    """Invalid or unsafe input; messages never include caller-supplied values."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError, InputError) as exc:
        raise InputError("validator_schema_unavailable") from exc
    Draft202012Validator.check_schema(schema)
    return schema


def _canonical_date(value: Any, path: str, issues: list[str]) -> date | None:
    if not isinstance(value, str):
        issues.append(f"{path}:invalid_date")
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        issues.append(f"{path}:invalid_date")
        return None
    if parsed.isoformat() != value:
        issues.append(f"{path}:invalid_date")
        return None
    return parsed


def _nonempty_items(section: dict[str, Any], name: str, issues: list[str]) -> list[dict[str, Any]]:
    items = section.get("items")
    if not isinstance(items, list) or not items:
        issues.append(f"sections.{name}:entries_required")
        return []
    return [item for item in items if isinstance(item, dict)]


def _check_status(sections: dict[str, Any], name: str, issues: list[str]) -> dict[str, Any] | None:
    section = sections.get(name)
    if not isinstance(section, dict):
        issues.append(f"sections.{name}:missing")
        return None
    if section.get("status") != "assessed":
        issues.append(f"sections.{name}:unassessed")
    return section


def _unique_refs(items: list[dict[str, Any]], key: str, path: str, issues: list[str]) -> set[str]:
    refs = [item.get(key) for item in items]
    if len(refs) != len(set(refs)):
        issues.append(f"{path}:duplicate_identifiers")
    return {value for value in refs if isinstance(value, str)}


def _semantics(payload: dict[str, Any], issues: list[str]) -> None:
    product_id = payload.get("product_id")
    as_of = _canonical_date(payload.get("as_of_date"), "as_of_date", issues)
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        issues.append("sections:missing")
        return

    for name in SECTIONS:
        if name not in sections:
            issues.append(f"sections.{name}:missing")

    for name in ("purposes", "data_categories", "processing_operations", "processors", "retention", "access_roles"):
        section = _check_status(sections, name, issues)
        if section is None:
            continue
        items = section.get("items", [])
        if not isinstance(items, list):
            items = []
        if name == "processors":
            if not items and section.get("no_processors_declared") is not True:
                issues.append("sections.processors:processor_inventory_empty")
        else:
            items = _nonempty_items(section, name, issues)
        if name == "data_categories":
            for index, item in enumerate(items):
                if item.get("classification") == "unassessed":
                    issues.append(f"sections.data_categories.items[{index}]:classification_unassessed")
        elif name == "processing_operations":
            for index, item in enumerate(items):
                if item.get("product_id") != product_id:
                    issues.append(f"sections.processing_operations.items[{index}]:cross_product_scope")
        elif name == "processors":
            if section.get("no_processors_declared") is True and items:
                issues.append("sections.processors:contradictory_no_processors_declaration")
            for index, item in enumerate(items):
                if item.get("product_id") != product_id:
                    issues.append(f"sections.processors.items[{index}]:cross_product_scope")
                if item.get("role") == "unassessed" or item.get("terms_status") == "unassessed":
                    issues.append(f"sections.processors.items[{index}]:assessment_unresolved")
                if item.get("terms_status") == "pending_counsel":
                    issues.append(f"sections.processors.items[{index}]:counsel_gate_pending")
        elif name == "retention":
            for index, item in enumerate(items):
                if item.get("decision_status") in {"unresolved", "unassessed"}:
                    issues.append(f"sections.retention.items[{index}]:decision_unresolved")

    basis = sections.get("lawful_basis")
    if not isinstance(basis, dict):
        issues.append("sections.lawful_basis:missing")
    elif basis.get("decision") == "unresolved":
        issues.append("sections.lawful_basis:decision_unresolved")

    consent = sections.get("consent")
    if not isinstance(consent, dict):
        issues.append("sections.consent:missing")
    elif consent.get("decision") == "unresolved":
        issues.append("sections.consent:decision_unresolved")

    purpose_section = sections.get("purposes", {})
    purpose_items = purpose_section.get("items", []) if isinstance(purpose_section, dict) else []
    purpose_refs = _unique_refs(purpose_items, "purpose_id", "sections.purposes.items", issues)
    category_section = sections.get("data_categories", {})
    category_items = category_section.get("items", []) if isinstance(category_section, dict) else []
    category_refs = _unique_refs(category_items, "category_id", "sections.data_categories.items", issues)
    operation_section = sections.get("processing_operations", {})
    operation_items = operation_section.get("items", []) if isinstance(operation_section, dict) else []
    _unique_refs(operation_items, "operation_id", "sections.processing_operations.items", issues)
    for index, operation in enumerate(operation_items):
        if operation.get("purpose_ref") not in purpose_refs:
            issues.append(f"sections.processing_operations.items[{index}]:purpose_reference_missing")
        if any(ref not in category_refs for ref in operation.get("data_category_refs", [])):
            issues.append(f"sections.processing_operations.items[{index}]:category_reference_missing")

    for index, item in enumerate(sections.get("retention", {}).get("items", [])):
        if item.get("category_ref") not in category_refs:
            issues.append(f"sections.retention.items[{index}]:category_reference_missing")

    residency = _check_status(sections, "residency_transfers", issues)
    if residency is not None:
        if not residency.get("known_locations"):
            issues.append("sections.residency_transfers:locations_missing")
        transfers = residency.get("transfers", [])
        if residency.get("no_transfers_declared") is True and transfers:
            issues.append("sections.residency_transfers:contradictory_no_transfers_declaration")
        if residency.get("no_transfers_declared") is not True and not transfers:
            issues.append("sections.residency_transfers:transfer_assessment_missing")
        for index, transfer in enumerate(transfers):
            if transfer.get("assessment_status") == "unassessed":
                issues.append(f"sections.residency_transfers.transfers[{index}]:assessment_unresolved")
            elif transfer.get("assessment_status") == "pending_counsel":
                issues.append(f"sections.residency_transfers.transfers[{index}]:counsel_gate_pending")

    procedure = sections.get("deletion_procedure")
    step_ids: set[str] = set()
    if not isinstance(procedure, dict):
        issues.append("sections.deletion_procedure:missing")
    else:
        if procedure.get("status") != "assessed":
            issues.append("sections.deletion_procedure:unassessed")
        if not procedure.get("steps"):
            issues.append("sections.deletion_procedure:steps_missing")
        step_ids = _unique_refs(procedure.get("steps", []), "step_id", "sections.deletion_procedure.steps", issues)
        system_refs = {item.get("system_ref") for item in operation_items}
        for index, step in enumerate(procedure.get("steps", [])):
            if step.get("system_ref") not in system_refs:
                issues.append(f"sections.deletion_procedure.steps[{index}]:system_reference_missing")

    test = sections.get("deletion_test")
    if not isinstance(test, dict):
        issues.append("sections.deletion_test:missing")
    else:
        if test.get("procedure_ref") not in step_ids:
            issues.append("sections.deletion_test:procedure_reference_missing")
        if test.get("product_id") != product_id:
            issues.append("sections.deletion_test:cross_product_scope")
        executed = _canonical_date(test.get("executed_on"), "sections.deletion_test.executed_on", issues)
        valid_until = _canonical_date(test.get("valid_until"), "sections.deletion_test.valid_until", issues)
        if test.get("result") != "pass":
            issues.append("sections.deletion_test:not_passed")
        if as_of and executed and executed > as_of:
            issues.append("sections.deletion_test:execution_after_as_of_date")
        if as_of and valid_until and valid_until < as_of:
            issues.append("sections.deletion_test:stale_evidence")
        if executed and valid_until and valid_until < executed:
            issues.append("sections.deletion_test:invalid_validity_window")
        expected = test.get("expected_result")
        observed = test.get("observed_result")
        if isinstance(expected, str) and isinstance(observed, str) and expected.strip() != observed.strip():
            issues.append("sections.deletion_test:expected_observed_mismatch")

    pia = sections.get("pia")
    if not isinstance(pia, dict):
        issues.append("sections.pia:missing")
    else:
        if pia.get("status") != "prepared":
            issues.append("sections.pia:unassessed")
        risks = pia.get("risks", [])
        if not risks:
            issues.append("sections.pia:risks_missing")
        for index, risk in enumerate(risks):
            if risk.get("likelihood") == "unassessed" or risk.get("impact") == "unassessed":
                issues.append(f"sections.pia.risks[{index}]:risk_rating_unassessed")


def evaluate(payload: Any) -> dict[str, Any]:
    """Return a safe, non-legal completeness report; never echo submitted values."""
    issues: list[str] = []
    if type(payload) is not dict:
        return {"status": "incomplete", "issues": ["document:object_required"], "verified": False}
    try:
        validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
    except (InputError, ValueError):
        return {"status": "incomplete", "issues": ["validator:schema_unavailable"], "verified": False}
    errors = sorted(
        validator.iter_errors(payload), key=lambda error: (list(map(str, error.absolute_path)), error.validator or "")
    )
    if errors:
        issues.extend(f"document:{'.'.join(map(str, error.absolute_path)) or 'root'}:invalid_shape" for error in errors)
    else:
        _semantics(payload, issues)
    issues = sorted(set(issues))
    return {
        "status": "complete-for-counsel-review-unverified" if not issues else "incomplete",
        "issues": issues,
        "verified": False,
        "legal_reviewed": False,
    }


def load_document(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_unique_object)
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = load_document(args.document)
    except InputError as exc:
        print(
            json.dumps(
                {"status": "incomplete", "issues": [str(exc)], "verified": False, "legal_reviewed": False},
                sort_keys=True,
            )
        )
        return 1
    report = evaluate(payload)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "complete-for-counsel-review-unverified" else 1


if __name__ == "__main__":
    sys.exit(main())
