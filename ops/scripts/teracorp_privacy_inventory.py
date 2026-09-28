#!/usr/bin/env python3
"""The single Teracorp G7/LEG2 privacy-inventory gate: structure, references and deletion evidence.

Checks a local, founder/counsel-prepared inventory (schema v2) and the hashed deletion-test
evidence records emitted by ``teracorp_g7_erasure.py``. It never assesses compliance, never
contacts a service, and never echoes submitted values. Exit status: 0 only when the package is
structurally complete with no unresolved item, 1 when it is ``incomplete`` (any unresolved item,
including a failed, missing, stale or unverifiable deletion test), 2 when input is invalid.

``teracorp_g7_data_map.py`` is a deprecated alias of this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MAX_BYTES = 1_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_INTEGER_DIGITS = 20
MAX_RECORDS = 256
MAX_RETENTION_DAYS = 36_500
SHA256 = re.compile(r"^[a-f0-9]{64}$")
# Every identifier is a typed, generated opaque token: a name-like label cannot match.
ID_PATTERNS = {
    "prod": re.compile(r"^prod_[a-f0-9]{32}$"),
    "proc": re.compile(r"^proc_[a-f0-9]{32}$"),
    "ev": re.compile(r"^ev_[a-f0-9]{32}$"),
    "purp": re.compile(r"^purp_[a-f0-9]{32}$"),
    "flow": re.compile(r"^flow_[a-f0-9]{32}$"),
    "store": re.compile(r"^store_[a-f0-9]{32}$"),
    "key": re.compile(r"^key_[a-f0-9]{32}$"),
}
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
HIGH_SENSITIVITY = {"child_data", "payment_metadata"}
SERVICE_KINDS = {
    "first_party_hosting",
    "erp",
    "crm_messaging",
    "database",
    "payment",
    "email",
    "analytics",
    "llm",
    "backup_storage",
    "other",
}
HOSTING_REGIONS = {"ID", "SG", "US", "EU", "other", "unknown"}
PURPOSE_KINDS = {
    "service_delivery",
    "billing",
    "support",
    "marketing",
    "analytics",
    "security",
    "legal_obligation",
    "other",
}
STORE_KINDS = {"odoo_orm", "chatwoot_contact", "supabase_row", "backup_snapshot", "other"}
ERASURE_MODES = {"erase", "retention_bound"}
RETENTION_TRIGGERS = {"account_closure", "last_activity", "transaction_date", "collection", "backup_taken"}
RETENTION_DISPOSITIONS = {"delete", "anonymise", "expire"}
RECORD_KIND = "teracorp_g7_deletion_evidence"
RECORD_ENVIRONMENTS = {"local_fake", "isolated_test", "production_drill"}
PASSING_OUTCOMES = {"absent", "tombstoned"}
STORE_OUTCOMES = PASSING_OUTCOMES | {
    "residue",
    "tombstone_missing",
    "retention_exceeded",
    "no_adapter",
    "seed_not_observable",
    "erasure_error",
}
OK_STATUS = "inventory-structured-for-founder-counsel-review-unverified"
INCOMPLETE_STATUS = "incomplete"


class InputError(ValueError):
    """Input cannot safely be treated as a structural inventory or evidence record."""


# --------------------------------------------------------------------------- primitives


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


def _typed_id(value: Any, kind: str, where: str) -> str:
    if not isinstance(value, str) or not ID_PATTERNS[kind].fullmatch(value):
        raise InputError(f"{where} must be an opaque generated {kind}_ identifier")
    return value


def _product_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_PATTERNS["prod"].fullmatch(value):
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


def _enum(value: Any, options: set[str], where: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise InputError(f"{where} is invalid")
    return value


def _int(value: Any, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InputError(f"{where} must be an integer in {low}..{high}")
    return value


def _ids(value: Any, kind: str, where: str, *, required: bool = False, maximum: int = 32) -> list[str]:
    if type(value) is not list or len(value) > maximum or (required and not value):
        raise InputError(f"{where} must be a bounded {'non-empty ' if required else ''}array")
    result = [_typed_id(item, kind, f"{where}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise InputError(f"{where} contains duplicate identifiers")
    return result


def _categories(value: Any, where: str) -> list[str]:
    if (
        type(value) is not list
        or not 1 <= len(value) <= 16
        or any(not isinstance(category, str) for category in value)
        or len(set(value)) != len(value)
    ):
        raise InputError(f"{where} must be a non-empty unique bounded list")
    if any(category not in CATEGORIES for category in value):
        raise InputError(f"{where} contains an unknown category")
    return list(value)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_map_digest(stores: list[dict[str, Any]]) -> str:
    """Digest of a product's store map; binds a deletion record to the exact map it tested."""
    ordered = sorted(stores, key=lambda store: store["store_id"])
    return sha256_hex(canonical_json(ordered))


# --------------------------------------------------------------------------- evidence records


@dataclass(frozen=True)
class DeletionRecord:
    digest: str
    product_id: str
    executed_on: date
    valid_until: date
    environment: str
    status: str
    store_map_sha256: str
    outcomes: dict[str, str]
    body: dict[str, Any]
    signature: dict[str, Any] | None


def record_body_digest(body: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(body))


def sign_body(body: dict[str, Any], key: bytes) -> str:
    return hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()


def parse_record(raw: Any, where: str = "record") -> DeletionRecord:
    """Strictly parse one deletion-evidence record emitted by the erasure harness."""
    record = _object(raw, where, {"body", "signature"})
    body = _object(
        record["body"],
        f"{where}.body",
        {
            "record_version",
            "kind",
            "product_id",
            "executed_on",
            "valid_until",
            "environment",
            "status",
            "subject_digest",
            "store_map_sha256",
            "stores",
        },
    )
    if type(body["record_version"]) is not int or body["record_version"] != 1 or body["kind"] != RECORD_KIND:
        raise InputError(f"{where} is not a v1 deletion-evidence record")
    product_id = _product_id(body["product_id"], f"{where}.product_id")
    executed = _day(body["executed_on"], f"{where}.executed_on")
    valid_until = _day(body["valid_until"], f"{where}.valid_until")
    if valid_until < executed:
        raise InputError(f"{where} validity window ends before execution")
    environment = _enum(body["environment"], RECORD_ENVIRONMENTS, f"{where}.environment")
    status = _enum(body["status"], {"passed", "failed"}, f"{where}.status")
    for name in ("subject_digest", "store_map_sha256"):
        if not isinstance(body[name], str) or not SHA256.fullmatch(body[name]):
            raise InputError(f"{where}.{name} must be a sha256 digest")
    stores = body["stores"]
    if type(stores) is not list or not 1 <= len(stores) <= 64:
        raise InputError(f"{where}.stores must contain 1..64 stores")
    outcomes: dict[str, str] = {}
    for index, item_raw in enumerate(stores):
        item = _object(
            item_raw,
            f"{where}.stores[{index}]",
            {"store_id", "store_kind", "erasure_mode", "outcome", "residue_count"},
        )
        store_id = _typed_id(item["store_id"], "store", f"{where}.stores[{index}].store_id")
        if store_id in outcomes:
            raise InputError(f"{where}.stores contains duplicate stores")
        _enum(item["store_kind"], STORE_KINDS, f"{where}.stores[{index}].store_kind")
        _enum(item["erasure_mode"], ERASURE_MODES, f"{where}.stores[{index}].erasure_mode")
        outcomes[store_id] = _enum(item["outcome"], STORE_OUTCOMES, f"{where}.stores[{index}].outcome")
        _int(item["residue_count"], f"{where}.stores[{index}].residue_count", 0, 1_000_000)
    all_pass = all(outcome in PASSING_OUTCOMES for outcome in outcomes.values())
    if (status == "passed") != all_pass:
        raise InputError(f"{where}.status contradicts its store outcomes")
    signature = record["signature"]
    if signature is not None:
        signature = _object(signature, f"{where}.signature", {"alg", "key_id", "value"})
        if signature["alg"] != "hmac-sha256" or not isinstance(signature["value"], str):
            raise InputError(f"{where}.signature is invalid")
        _typed_id(signature["key_id"], "key", f"{where}.signature.key_id")
        if not SHA256.fullmatch(signature["value"]):
            raise InputError(f"{where}.signature is invalid")
    return DeletionRecord(
        digest=record_body_digest(body),
        product_id=product_id,
        executed_on=executed,
        valid_until=valid_until,
        environment=environment,
        status=status,
        store_map_sha256=body["store_map_sha256"],
        outcomes=outcomes,
        body=body,
        signature=signature,
    )


# --------------------------------------------------------------------------- inventory


@dataclass
class ProductView:
    """Parsed, validated product used by the report and the document generator."""

    index: int
    product_id: str
    raw: dict[str, Any]
    stores: list[dict[str, Any]]
    flows: list[dict[str, Any]]
    retention_rules: dict[str, dict[str, Any]]
    unresolved: set[str] = field(default_factory=set)
    deletion_record: DeletionRecord | None = None


@dataclass
class InventoryView:
    as_of: date
    processors: dict[str, dict[str, Any]]
    purposes: dict[str, dict[str, str]]
    evidence: dict[str, dict[str, Any]]
    products: list[ProductView]
    package_unresolved: set[str]
    report: dict[str, Any]


def _check_deletion_record(
    product: ProductView,
    evidence: dict[str, dict[str, Any]],
    records_by_digest: dict[str, DeletionRecord],
    cutoff: date,
    *,
    allow_local_fake: bool,
    hmac_key: bytes | None,
) -> None:
    raw = product.raw
    status = raw["deletion_test_status"]
    if status not in {"passed", "failed"}:
        return
    digests = {evidence[ref]["sha256"] for ref in raw["deletion_evidence_refs"]}
    matches = [records_by_digest[digest] for digest in sorted(digests) if digest in records_by_digest]
    issue = f"products[{product.index}]:deletion_record"
    if not matches:
        if status == "passed":
            product.unresolved.add(f"{issue}:missing")
        return
    if len(matches) > 1:
        product.unresolved.add(f"{issue}:ambiguous")
        return
    record = matches[0]
    product.deletion_record = record
    if record.product_id != product.product_id:
        product.unresolved.add(f"{issue}:cross_product_scope")
        return
    if record.status != status:
        product.unresolved.add(f"{issue}:contradicts_inventory_status")
    if record.status == "failed":
        product.unresolved.add(f"{issue}:failed")
    if record.executed_on.isoformat() != raw["deletion_tested_on"]:
        product.unresolved.add(f"{issue}:date_mismatch")
    if record.executed_on > cutoff:
        product.unresolved.add(f"{issue}:after_as_of_date")
    if record.valid_until < cutoff:
        product.unresolved.add(f"{issue}:stale")
    if record.store_map_sha256 != store_map_digest(product.stores):
        product.unresolved.add(f"{issue}:store_map_mismatch")
    if set(record.outcomes) != {store["store_id"] for store in product.stores}:
        product.unresolved.add(f"{issue}:store_coverage_mismatch")
    if record.environment == "local_fake" and not allow_local_fake:
        product.unresolved.add(f"{issue}:local_fake_environment")
    if hmac_key is not None:
        signature = record.signature
        if signature is None or not hmac.compare_digest(signature["value"], sign_body(record.body, hmac_key)):
            product.unresolved.add(f"{issue}:signature_invalid")


def inspect(
    payload: Any,
    as_of: str,
    records: list[DeletionRecord] | None = None,
    *,
    allow_local_fake: bool = False,
    hmac_key: bytes | None = None,
) -> InventoryView:
    """Validate an inventory package; raise InputError for structural faults."""
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
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise InputError(f"schema_version must be integer {SCHEMA_VERSION}")
    if _day(root["as_of_date"], "as_of_date") != cutoff:
        raise InputError("as_of must match package as_of_date")
    processor_state = _enum(
        root["processor_inventory_status"], {"complete", "none_declared", "unknown"}, "processor_inventory_status"
    )
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

    package_unresolved: set[str] = set()
    processors: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(processors_in):
        where = f"processors[{index}]"
        item = _object(
            raw, where, {"processor_id", "inventory_status", "service_kind", "hosting_region", "evidence_refs"}
        )
        key = _typed_id(item["processor_id"], "proc", f"{where}.processor_id")
        status = _enum(item["inventory_status"], {"identified", "unknown"}, f"{where}.inventory_status")
        if key in processors:
            raise InputError(f"{where} has a duplicate processor identity")
        _enum(item["service_kind"], SERVICE_KINDS, f"{where}.service_kind")
        region = _enum(item["hosting_region"], HOSTING_REGIONS, f"{where}.hosting_region")
        _ids(item["evidence_refs"], "ev", f"{where}.evidence_refs", required=status == "identified")
        if region == "unknown":
            package_unresolved.add(f"{where}:hosting_region:unknown")
        processors[key] = item
    if (processor_state == "none_declared") != (len(processors) == 0):
        raise InputError("none_declared must correspond exactly to an empty processor inventory")
    if processor_state == "complete" and any(item["inventory_status"] == "unknown" for item in processors.values()):
        raise InputError("complete processor inventory must contain identified entries only")

    evidence: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(evidence_in):
        where = f"evidence_refs[{index}]"
        item = _object(raw, where, {"evidence_ref_id", "sha256", "observed_on", "product_id"})
        key = _typed_id(item["evidence_ref_id"], "ev", f"{where}.evidence_ref_id")
        if key in evidence or not isinstance(item["sha256"], str) or not SHA256.fullmatch(item["sha256"]):
            raise InputError(f"{where} has duplicate identity or invalid digest")
        observed = _day(item["observed_on"], f"{where}.observed_on")
        if observed > cutoff:
            raise InputError(f"{where} is after as_of_date")
        if item["product_id"] is not None:
            _product_id(item["product_id"], f"{where}.product_id")
        evidence[key] = {"sha256": item["sha256"], "observed_on": observed, "product_id": item["product_id"]}
    linked_refs = 0
    for index, item in enumerate(processors_in):
        refs = set(item["evidence_refs"])
        linked_refs += len(refs)
        if not refs <= evidence.keys():
            raise InputError(f"processors[{index}] references missing evidence")
        if any(evidence[ref]["product_id"] is not None for ref in refs):
            raise InputError(f"processors[{index}] requires package-scoped evidence")

    purposes: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(purpose_registry):
        where = f"purpose_registry[{index}]"
        item = _object(raw, where, {"product_id", "purpose_id", "purpose_kind"})
        product_id = _product_id(item["product_id"], f"{where}.product_id")
        purpose_id = _typed_id(item["purpose_id"], "purp", f"{where}.purpose_id")
        _enum(item["purpose_kind"], PURPOSE_KINDS, f"{where}.purpose_kind")
        scoped = purposes.setdefault(product_id, {})
        if purpose_id in scoped:
            raise InputError("purpose_id values must be unique within their product scope")
        scoped[purpose_id] = item["purpose_kind"]

    records_by_digest: dict[str, DeletionRecord] = {}
    for record in records or []:
        if record.digest in records_by_digest:
            raise InputError("duplicate deletion-evidence record supplied")
        records_by_digest[record.digest] = record

    product_ids: set[str] = set()
    flow_ids: set[str] = set()
    store_ids: set[str] = set()
    products: list[ProductView] = []
    for index, raw in enumerate(products_in):
        where = f"products[{index}]"
        item = _object(
            raw,
            where,
            {
                "product_id",
                "inventory_status",
                "consent_status",
                "consent_evidence_refs",
                "retention_status",
                "retention_evidence_refs",
                "retention_rules",
                "deletion_test_status",
                "deletion_tested_on",
                "deletion_evidence_refs",
                "pia_status",
                "pia_evidence_refs",
                "stores",
                "data_flows",
            },
        )
        product_id = _product_id(item["product_id"], f"{where}.product_id")
        if product_id in product_ids:
            raise InputError("product_id values must be unique")
        product_ids.add(product_id)
        unresolved: set[str] = set()
        enums = {
            "inventory_status": {"complete", "unknown"},
            "consent_status": {"documented", "not_applicable_asserted", "unresolved"},
            "retention_status": {"documented", "unresolved"},
            "deletion_test_status": {"passed", "failed", "not_run", "unknown"},
            "pia_status": {"completed", "pending", "not_required_asserted", "unknown"},
        }
        for name, options in enums.items():
            value = _enum(item[name], options, f"{where}.{name}")
            if value in {"unknown", "unresolved", "pending", "failed", "not_run"}:
                unresolved.add(f"{where}:{name}:{value}")
        for name in ("consent_evidence_refs", "retention_evidence_refs", "deletion_evidence_refs", "pia_evidence_refs"):
            refs = _ids(item[name], "ev", f"{where}.{name}")
            linked_refs += len(refs)
            if not set(refs) <= evidence.keys():
                raise InputError(f"{where}.{name} references missing evidence")
            if any(evidence[ref]["product_id"] != product_id for ref in refs):
                raise InputError(f"{where}.{name} references evidence outside this product scope")
        for status_name, ref_name in (
            ("consent_status", "consent_evidence_refs"),
            ("retention_status", "retention_evidence_refs"),
            ("pia_status", "pia_evidence_refs"),
        ):
            if item[status_name] in {"documented", "completed"} and not item[ref_name]:
                raise InputError(f"{where}.{status_name} requires evidence references")
        deletion_status, tested_on = item["deletion_test_status"], item["deletion_tested_on"]
        deletion_refs = item["deletion_evidence_refs"]
        if deletion_status in {"passed", "failed"}:
            if tested_on is None or not deletion_refs:
                raise InputError(f"{where} executed deletion test requires date and evidence")
            deletion_date = _day(tested_on, f"{where}.deletion_tested_on")
            if deletion_date > cutoff:
                raise InputError(f"{where}.deletion_tested_on is after as_of_date")
            if any(evidence[ref]["observed_on"] < deletion_date for ref in deletion_refs):
                raise InputError(f"{where} deletion evidence predates the recorded test")
        elif tested_on is not None or deletion_refs:
            raise InputError(f"{where} unexecuted deletion test must not carry evidence or a date")

        stores_in = item["stores"]
        if type(stores_in) is not list or len(stores_in) > 64:
            raise InputError(f"{where}.stores must be a bounded array")
        stores: list[dict[str, Any]] = []
        own_store_ids: set[str] = set()
        for store_index, store_raw in enumerate(stores_in):
            store_where = f"{where}.stores[{store_index}]"
            store = _object(
                store_raw,
                store_where,
                {"store_id", "store_kind", "processor_id", "data_categories", "erasure_mode", "retention_days"},
            )
            store_id = _typed_id(store["store_id"], "store", f"{store_where}.store_id")
            if store_id in store_ids:
                raise InputError("store_id values must be unique across the package")
            store_ids.add(store_id)
            own_store_ids.add(store_id)
            _enum(store["store_kind"], STORE_KINDS, f"{store_where}.store_kind")
            processor_id = _typed_id(store["processor_id"], "proc", f"{store_where}.processor_id")
            if processor_id not in processors:
                raise InputError(f"{store_where} references an unknown processor")
            _categories(store["data_categories"], f"{store_where}.data_categories")
            mode = _enum(store["erasure_mode"], ERASURE_MODES, f"{store_where}.erasure_mode")
            if mode == "retention_bound":
                _int(store["retention_days"], f"{store_where}.retention_days", 1, MAX_RETENTION_DAYS)
            elif store["retention_days"] is not None:
                raise InputError(f"{store_where}.retention_days applies only to retention_bound stores")
            if store["store_kind"] == "other":
                unresolved.add(f"{store_where}:store_kind:other")
            stores.append(store)

        flows_in = item["data_flows"]
        if type(flows_in) is not list or len(flows_in) > 512:
            raise InputError(f"{where}.data_flows must be a bounded array")
        if item["inventory_status"] == "complete" and (not flows_in or not stores):
            raise InputError(f"{where} complete inventory requires data flow(s) and store(s)")
        flows: list[dict[str, Any]] = []
        for flow_index, flow_raw in enumerate(flows_in):
            flow_where = f"{where}.data_flows[{flow_index}]"
            flow = _object(
                flow_raw,
                flow_where,
                {"flow_id", "processor_id", "purpose_id", "store_id", "data_categories", "evidence_refs"},
            )
            flow_id = _typed_id(flow["flow_id"], "flow", f"{flow_where}.flow_id")
            if flow_id in flow_ids:
                raise InputError("flow_id values must be unique across the package")
            flow_ids.add(flow_id)
            processor_id = _typed_id(flow["processor_id"], "proc", f"{flow_where}.processor_id")
            if processor_id not in processors:
                raise InputError(f"{flow_where} references an unknown processor")
            if processors[processor_id]["inventory_status"] == "unknown":
                unresolved.add(f"{flow_where}:processor:unknown")
            purpose_id = _typed_id(flow["purpose_id"], "purp", f"{flow_where}.purpose_id")
            if purpose_id not in purposes.get(product_id, {}):
                raise InputError(f"{flow_where} references an unknown or cross-product purpose")
            store_id = _typed_id(flow["store_id"], "store", f"{flow_where}.store_id")
            if store_id not in own_store_ids:
                raise InputError(f"{flow_where} references an unknown or cross-product store")
            _categories(flow["data_categories"], f"{flow_where}.data_categories")
            refs = _ids(flow["evidence_refs"], "ev", f"{flow_where}.evidence_refs", required=True)
            linked_refs += len(refs)
            if not set(refs) <= evidence.keys():
                raise InputError(f"{flow_where}.evidence_refs references missing evidence")
            if any(evidence[ref]["product_id"] != product_id for ref in refs):
                raise InputError(f"{flow_where}.evidence_refs references evidence outside this product scope")
            flows.append(flow)

        rules_in = item["retention_rules"]
        if type(rules_in) is not list or len(rules_in) > 16:
            raise InputError(f"{where}.retention_rules must be a bounded array")
        rules: dict[str, dict[str, Any]] = {}
        for rule_index, rule_raw in enumerate(rules_in):
            rule_where = f"{where}.retention_rules[{rule_index}]"
            rule = _object(rule_raw, rule_where, {"data_category", "retention_days", "trigger", "disposition"})
            category = _enum(rule["data_category"], CATEGORIES, f"{rule_where}.data_category")
            if category in rules:
                raise InputError(f"{where}.retention_rules has duplicate categories")
            if rule["retention_days"] is not None:
                _int(rule["retention_days"], f"{rule_where}.retention_days", 1, MAX_RETENTION_DAYS)
            _enum(rule["trigger"], RETENTION_TRIGGERS, f"{rule_where}.trigger")
            _enum(rule["disposition"], RETENTION_DISPOSITIONS, f"{rule_where}.disposition")
            rules[category] = rule
        held = {category for store in stores for category in store["data_categories"]}
        held |= {category for flow in flows for category in flow["data_categories"]}
        for category in sorted(held):
            rule = rules.get(category)
            if rule is None:
                unresolved.add(f"{where}:retention_rule:{category}:missing")
            elif rule["retention_days"] is None:
                unresolved.add(f"{where}:retention_rule:{category}:period_unresolved")

        if item["inventory_status"] == "unknown":
            unresolved.add(f"{where}:inventory_status:unknown")
        if item["consent_status"] == "not_applicable_asserted":
            unresolved.add(f"{where}:consent_status:founder_asserted_unverified")
        if item["pia_status"] == "not_required_asserted":
            unresolved.add(f"{where}:pia_status:founder_asserted_unverified")
        product = ProductView(index, product_id, item, stores, flows, rules, unresolved)
        _check_deletion_record(
            product, evidence, records_by_digest, cutoff, allow_local_fake=allow_local_fake, hmac_key=hmac_key
        )
        products.append(product)

    if not set(purposes) <= product_ids:
        raise InputError("purpose_registry references an unknown product")
    if not {item["product_id"] for item in evidence.values() if item["product_id"] is not None} <= product_ids:
        raise InputError("evidence scope references an unknown product")
    if processor_state == "unknown":
        package_unresolved.add("processor_inventory_status:unknown")
    if processor_state == "none_declared":
        package_unresolved.add("processor_inventory_status:none_declared_unverified")
    unresolved_all = set(package_unresolved)
    for product in products:
        unresolved_all |= product.unresolved
    report = {
        "status": INCOMPLETE_STATUS if unresolved_all else OK_STATUS,
        "as_of_date": cutoff.isoformat(),
        "verified": False,
        "legal_reviewed": False,
        "deletion_verified": False,
        "product_count": len(product_ids),
        "processor_count": len(processors),
        "store_count": len(store_ids),
        "evidence_reference_count": len(evidence),
        "linked_evidence_reference_count": linked_refs,
        "deletion_records_matched": sum(1 for product in products if product.deletion_record is not None),
        "unresolved_count": len(unresolved_all),
        "unresolved": sorted(unresolved_all),
    }
    return InventoryView(cutoff, processors, purposes, evidence, products, package_unresolved, report)


def validate(
    payload: Any,
    as_of: str,
    records: list[DeletionRecord] | None = None,
    *,
    allow_local_fake: bool = False,
    hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """Return the aggregate, unverified, non-echoing report for an inventory package."""
    return inspect(payload, as_of, records, allow_local_fake=allow_local_fake, hmac_key=hmac_key).report


# --------------------------------------------------------------------------- bounded loading


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
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_int=_bounded_json_integer)
    except InputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise InputError("input_unreadable_or_invalid_json") from exc


def load_records(paths: list[Path]) -> list[DeletionRecord]:
    if len(paths) > MAX_RECORDS:
        raise InputError("too_many_deletion_records")
    return [parse_record(load(path), f"deletion_record[{index}]") for index, path in enumerate(paths)]


def load_key(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            key = stream.read(4097).strip()
    except OSError as exc:
        raise InputError("signing_key_unreadable") from exc
    if not 32 <= len(key) <= 4096:
        raise InputError("signing_key_must_be_32_to_4096_bytes")
    return key


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", required=True, help="explicit YYYY-MM-DD evaluation date")
    parser.add_argument(
        "--deletion-record",
        action="append",
        default=[],
        type=Path,
        help="deletion-evidence record from teracorp_g7_erasure.py (repeatable)",
    )
    parser.add_argument(
        "--allow-local-fake-evidence",
        action="store_true",
        help="accept records produced against local fakes (built-local only; never for a real gate)",
    )
    parser.add_argument("--hmac-key-file", type=Path, help="require and verify HMAC-SHA256 record signatures")
    parser.add_argument("input", type=Path, help="explicit local JSON inventory")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        report = validate(
            load(args.input),
            args.as_of,
            load_records(args.deletion_record),
            allow_local_fake=args.allow_local_fake_evidence,
            hmac_key=load_key(args.hmac_key_file),
        )
    except InputError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc), "verified": False, "legal_reviewed": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == OK_STATUS else 1


if __name__ == "__main__":
    sys.exit(main())
