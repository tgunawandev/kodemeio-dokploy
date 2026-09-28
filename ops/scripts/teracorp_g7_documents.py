#!/usr/bin/env python3
"""Generate Teracorp G7 per-product documents from a validated v2 privacy inventory.

Writes, per product: ``data-map.md``, ``processors.md``, ``retention.md`` and
``pia-skeleton.md``. Documents contain only opaque identifiers and closed-vocabulary values from
the inventory, carry an ``UNVERIFIED`` banner, and list every unresolved item for the product.
They are review packets for the founder and qualified counsel, never legal conclusions.
Exit status: 0 when documents were written, 2 when the inventory is invalid (nothing written).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teracorp_privacy_inventory as inventory  # noqa: E402

DOCUMENTS = ("data-map.md", "processors.md", "retention.md", "pia-skeleton.md")
UNASSESSED = "UNASSESSED (founder/counsel)"


def _banner(title: str, view: inventory.InventoryView, product: inventory.ProductView, digest: str) -> list[str]:
    status = "INCOMPLETE" if product.unresolved or view.package_unresolved else "STRUCTURALLY COMPLETE"
    lines = [
        f"# {title} — `{product.product_id}`",
        "",
        f"> **UNVERIFIED — {status}.** Generated from inventory sha256 `{digest}` as of "
        f"{view.as_of.isoformat()}. Not legal advice, not a compliance finding, not counsel-reviewed.",
        "",
    ]
    unresolved = sorted(product.unresolved | view.package_unresolved)
    if unresolved:
        lines += ["## Unresolved items", ""] + [f"- `{item}`" for item in unresolved] + [""]
    return lines


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows] or ["| " + " | ".join("—" for _ in header) + " |"]
    return lines + [""]


def _processor_ids(product: inventory.ProductView) -> list[str]:
    return sorted(
        {store["processor_id"] for store in product.stores} | {flow["processor_id"] for flow in product.flows}
    )


def data_map(view: inventory.InventoryView, product: inventory.ProductView, digest: str) -> str:
    raw = product.raw
    purposes = view.purposes.get(product.product_id, {})
    lines = _banner("Data map", view, product, digest)
    lines += [
        f"- Inventory status: `{raw['inventory_status']}`",
        f"- Consent status: `{raw['consent_status']}` ({len(raw['consent_evidence_refs'])} evidence ref(s))",
        "",
        "## Stores",
        "",
    ]
    lines += _table(
        ("Store", "Kind", "Processor", "Categories", "Erasure mode", "Retention bound (days)"),
        [
            (
                f"`{store['store_id']}`",
                store["store_kind"],
                f"`{store['processor_id']}`",
                ", ".join(store["data_categories"]),
                store["erasure_mode"],
                "—" if store["retention_days"] is None else str(store["retention_days"]),
            )
            for store in product.stores
        ],
    )
    lines += ["## Data flows", ""]
    lines += _table(
        ("Flow", "Purpose", "Purpose kind", "Processor", "Store", "Categories", "Evidence refs"),
        [
            (
                f"`{flow['flow_id']}`",
                f"`{flow['purpose_id']}`",
                purposes.get(flow["purpose_id"], "?"),
                f"`{flow['processor_id']}`",
                f"`{flow['store_id']}`",
                ", ".join(flow["data_categories"]),
                str(len(flow["evidence_refs"])),
            )
            for flow in product.flows
        ],
    )
    lines += ["## Deletion test", ""] + _deletion_lines(product)
    return "\n".join(lines)


def _deletion_lines(product: inventory.ProductView) -> list[str]:
    raw = product.raw
    lines = [
        f"- Inventory status: `{raw['deletion_test_status']}`; tested on: `{raw['deletion_tested_on'] or '—'}`",
    ]
    record = product.deletion_record
    if record is None:
        lines.append("- Evidence record: **none matched** (supply it with `--deletion-record`)")
    else:
        lines += [
            f"- Evidence record sha256: `{record.digest}` ({record.environment}, {record.status})",
            f"- Executed on `{record.executed_on.isoformat()}`, valid until `{record.valid_until.isoformat()}`",
        ]
        lines += [f"  - `{store_id}`: {outcome}" for store_id, outcome in sorted(record.outcomes.items())]
    return lines + [""]


def processors(view: inventory.InventoryView, product: inventory.ProductView, digest: str) -> str:
    lines = _banner("Processor list", view, product, digest)
    rows = []
    for processor_id in _processor_ids(product):
        item = view.processors[processor_id]
        stores = sum(1 for store in product.stores if store["processor_id"] == processor_id)
        flows = sum(1 for flow in product.flows if flow["processor_id"] == processor_id)
        rows.append(
            (
                f"`{processor_id}`",
                item["service_kind"],
                item["hosting_region"],
                item["inventory_status"],
                str(stores),
                str(flows),
                str(len(item["evidence_refs"])),
            )
        )
    lines += _table(("Processor", "Service kind", "Region", "Status", "Stores", "Flows", "Evidence refs"), rows)
    lines += [
        "Contract/DPA terms, sub-processors and transfer mechanisms: " + UNASSESSED + ".",
        "Map opaque processor IDs to vendors only in the access-controlled founder register.",
        "",
    ]
    return "\n".join(lines)


def retention(view: inventory.InventoryView, product: inventory.ProductView, digest: str) -> str:
    raw = product.raw
    lines = _banner("Retention table", view, product, digest)
    lines += [f"- Retention status: `{raw['retention_status']}`", "", "## Rules by data category", ""]
    held = sorted(
        {category for store in product.stores for category in store["data_categories"]}
        | {category for flow in product.flows for category in flow["data_categories"]}
        | set(product.retention_rules)
    )
    rows = []
    for category in held:
        rule = product.retention_rules.get(category)
        if rule is None:
            rows.append((category, "MISSING", "—", "—"))
        else:
            days = "UNRESOLVED" if rule["retention_days"] is None else str(rule["retention_days"])
            rows.append((category, days, rule["trigger"], rule["disposition"]))
    lines += _table(("Category", "Retention (days)", "Trigger", "Disposition"), rows)
    lines += ["## Retention-bound stores (backups)", ""]
    lines += _table(
        ("Store", "Kind", "Expiry bound (days)", "Erasure handling"),
        [
            (f"`{store['store_id']}`", store["store_kind"], str(store["retention_days"]), "digest tombstone + expiry")
            for store in product.stores
            if store["erasure_mode"] == "retention_bound"
        ],
    )
    return "\n".join(lines)


def pia(view: inventory.InventoryView, product: inventory.ProductView, digest: str) -> str:
    raw = product.raw
    lines = _banner("PIA skeleton", view, product, digest)
    categories = sorted({category for store in product.stores for category in store["data_categories"]})
    sensitive = [category for category in categories if category in inventory.HIGH_SENSITIVITY]
    lines += [
        f"- PIA status: `{raw['pia_status']}` ({len(raw['pia_evidence_refs'])} evidence ref(s))",
        f"- Data categories: {', '.join(categories) or '—'}",
        f"- High-sensitivity categories requiring counsel review: {', '.join(sensitive) or 'none declared'}",
        f"- Processors involved: {len(_processor_ids(product))}; stores: {len(product.stores)}",
        "",
        "## Risk register",
        "",
    ]
    lines += _table(
        ("Store", "Category", "Likelihood", "Impact", "Mitigation", "Residual risk"),
        [
            (f"`{store['store_id']}`", category, UNASSESSED, UNASSESSED, UNASSESSED, UNASSESSED)
            for store in product.stores
            for category in store["data_categories"]
        ],
    )
    lines += ["## Deletion capability", ""] + _deletion_lines(product)
    lines += [
        "## Sign-off",
        "",
        "- Founder: " + UNASSESSED,
        "- Qualified Indonesian counsel (PDP lawful basis, consent, children, transfers): " + UNASSESSED,
        "",
    ]
    return "\n".join(lines)


RENDERERS = {"data-map.md": data_map, "processors.md": processors, "retention.md": retention, "pia-skeleton.md": pia}


def render(view: inventory.InventoryView, digest: str) -> dict[str, dict[str, str]]:
    return {
        product.product_id: {name: RENDERERS[name](view, product, digest) + "\n" for name in DOCUMENTS}
        for product in view.products
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    inventory.add_common_arguments(parser)
    parser.add_argument("--out", required=True, type=Path, help="output directory (one folder per product)")
    args = parser.parse_args(argv)
    try:
        payload = inventory.load(args.input)
        view = inventory.inspect(
            payload,
            args.as_of,
            inventory.load_records(args.deletion_record),
            allow_local_fake=args.allow_local_fake_evidence,
            hmac_key=inventory.load_key(args.hmac_key_file),
        )
    except inventory.InputError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 2
    except OSError:
        print(json.dumps({"status": "invalid", "error": "filesystem_error"}))
        return 2
    digest = inventory.sha256_hex(inventory.canonical_json(payload))
    written = []
    try:
        for product_id, documents in render(view, digest).items():
            folder = args.out / product_id
            folder.mkdir(parents=True, exist_ok=True)
            for name, text in documents.items():
                (folder / name).write_text(text, encoding="utf-8")
                written.append(str(folder / name))
    except OSError:
        print(json.dumps({"status": "invalid", "error": "filesystem_error"}))
        return 2
    print(json.dumps({"inventory_status": view.report["status"], "written": written}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
