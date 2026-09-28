#!/usr/bin/env python3
"""Reconcile tracked affiliate commissions against an advertiser statement (offline, synthetic).

Tracked commissions come from an accepted ``affiliate.v1`` document (deduplicated per event and per
``(advertiser_id, order_ref)``); the advertiser side is an ``affiliate-statement.v1`` document. Rows
are matched by opaque order reference and reported as matched, missing_from_statement,
extra_in_statement or amount_mismatch. Nothing is posted, paid or sent anywhere.

Exit codes: 0 reconciled, 3 discrepancies found, 1 refused input.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from affiliate import (  # noqa: E402
    MAX_TOTAL_MINOR,
    ROOT,
    InputError,
    _date,
    _deduplicated_commissions,
    _deduplicated_events,
    _unique_object,
    load_document,
    reconcile,
)
from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

STATEMENT_SCHEMA_PATH = ROOT / "contracts" / "affiliate-statement.v1.schema.json"


def _statement_validator() -> Draft202012Validator:
    try:
        schema = json.loads(STATEMENT_SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, InputError, ValueError) as exc:
        raise InputError("statement_schema_unavailable") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _event_date(event: dict[str, Any]) -> date:
    return datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")).date()


def reconcile_statement(payload: Any, statement: Any) -> dict[str, Any]:
    tracked = reconcile(payload)  # raises InputError unless the tracking document is accepted
    if type(statement) is not dict or any(True for _ in _statement_validator().iter_errors(statement)):
        raise InputError("statement_invalid")
    advertiser = payload["advertiser"]
    if statement["advertiser_id"] != advertiser["advertiser_id"]:
        raise InputError("statement_advertiser_mismatch")
    if statement["currency"] != tracked["currency"]:
        raise InputError("statement_currency_mismatch")
    start, end = _date(statement["period_start"]), _date(statement["period_end"])
    if not start or not end or start > end:
        raise InputError("statement_period_invalid")

    reported: dict[str, int] = {}
    for line in statement["lines"]:
        if line["order_ref"] in reported:
            raise InputError("statement_duplicate_order_ref")
        reported[line["order_ref"]] = line["commission_minor"]
    statement_total = sum(reported.values())
    if statement_total > MAX_TOTAL_MINOR:
        raise InputError("statement_total_out_of_range")

    commissions, _ = _deduplicated_commissions(_deduplicated_events(payload["events"]), advertiser["advertiser_id"])
    computed: dict[str, int] = {}
    out_of_period = 0
    for event in commissions:
        if start <= _event_date(event) <= end:
            computed[event["order_ref"]] = event["commission_minor"]
        else:
            out_of_period += 1

    matched, missing, extra, mismatch = [], [], [], []
    for order_ref in sorted(computed.keys() | reported.keys()):
        ours, theirs = computed.get(order_ref), reported.get(order_ref)
        if theirs is None:
            missing.append({"order_ref": order_ref, "commission_minor": ours})
        elif ours is None:
            extra.append({"order_ref": order_ref, "commission_minor": theirs})
        elif ours == theirs:
            matched.append({"order_ref": order_ref, "commission_minor": ours})
        else:
            mismatch.append(
                {
                    "order_ref": order_ref,
                    "computed_minor": ours,
                    "statement_minor": theirs,
                    "difference_minor": ours - theirs,
                }
            )
    computed_total = sum(computed.values())
    return {
        "status": "reconciled" if not (missing or extra or mismatch) else "discrepancies",
        "candidate_id": payload["candidate_id"],
        "statement_ref": statement["statement_ref"],
        "advertiser_id": advertiser["advertiser_id"],
        "currency": tracked["currency"],
        "period_start": statement["period_start"],
        "period_end": statement["period_end"],
        "matched": matched,
        "missing_from_statement": missing,
        "extra_in_statement": extra,
        "amount_mismatch": mismatch,
        "computed_total_minor": computed_total,
        "statement_total_minor": statement_total,
        "difference_minor": computed_total - statement_total,
        "out_of_period_count": out_of_period,
        "tracked_events_sha256": tracked["source_events_sha256"],
        "synthetic": True,
        "payments_created": False,
        "posted_to_ledger": False,
        "statement_authenticated": False,
        "verified": False,
        "manual_review_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracking", type=Path, help="affiliate.v1 document with tracked events")
    parser.add_argument("statement", type=Path, help="affiliate-statement.v1 advertiser statement")
    args = parser.parse_args(argv)
    try:
        report = reconcile_statement(load_document(args.tracking), load_document(args.statement))
    except InputError as exc:
        print(json.dumps({"status": "blocked", "issues": [str(exc)], "manual_review_required": True}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "reconciled" else 3


if __name__ == "__main__":
    sys.exit(main())
