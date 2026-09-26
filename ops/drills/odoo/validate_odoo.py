#!/usr/bin/env python3
"""App-level validator for the Odoo restore drill (Task 7).

Runs INSIDE the `validator` container on the drill's internal Docker
network — it can reach `odoo:8069` (container DNS) but nothing outside the
host, which is exactly what the egress probe below checks for.

All checks are JSON-RPC over the authenticated web session (never XML-RPC,
never a direct Postgres connection) plus a filesystem read of the restored
filestore volume, mounted read-only at FILESTORE_ROOT. This is why only
`httpx` needed adding to the repo's Python dependencies — no DB driver.

Exit code: 0 if every check passes, 1 otherwise. Always writes one JSON
object to stdout (and to --out if given) describing what was checked, so a
failing drill still leaves evidence of which attachment ids or counts were
wrong.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import socket
import sys
from pathlib import Path

import httpx


class ValidationError(RuntimeError):
    pass


def rpc_call(client: httpx.Client, model: str, method: str, args: list, kwargs: dict | None = None):
    """POST /web/dataset/call_kw — the session-cookie JSON-RPC endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": model,
            "method": method,
            "args": args,
            "kwargs": kwargs or {},
        },
    }
    resp = client.post("/web/dataset/call_kw", json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise ValidationError(f"{model}.{method} failed: {body['error']}")
    return body["result"]


def authenticate(client: httpx.Client, db: str, login: str, password: str) -> int:
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"db": db, "login": login, "password": password},
    }
    resp = client.post("/web/session/authenticate", json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    result = body.get("result") or {}
    uid = result.get("uid")
    if "error" in body or not uid:
        raise ValidationError(f"authentication failed for {login}@{db}: {body}")
    return uid


def check_health(client: httpx.Client) -> bool:
    resp = client.get("/web/health", timeout=30)
    return resp.status_code == 200


def check_egress_blocked(host: str = "1.1.1.1", port: int = 443, timeout: float = 3.0) -> bool:
    """True if the outbound connection was refused/unreachable/timed out —
    i.e. the drill network really has no egress. False (a real problem) if
    the connection unexpectedly succeeded."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return False
    except OSError:
        return True


def filestore_path(root: Path, db: str, store_fname: str) -> Path:
    return root / "filestore" / db / store_fname


def sha1_of_file(path: Path) -> str | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("ODOO_URL", "http://odoo:8069"))
    parser.add_argument("--db", default=os.environ.get("ODOO_DB"), required=os.environ.get("ODOO_DB") is None)
    parser.add_argument("--user", default=os.environ.get("DRILL_USER", "drill_validator"))
    parser.add_argument("--password", default=os.environ.get("DRILL_PASS"))
    parser.add_argument("--expected-orders", type=int, default=int(os.environ.get("EXPECTED_ORDERS", "-1")))
    parser.add_argument("--sample", type=int, default=int(os.environ.get("SAMPLE", "20")))
    parser.add_argument("--filestore-root", default=os.environ.get("FILESTORE_ROOT", "/var/lib/odoo"))
    parser.add_argument(
        "--attachments-res-model",
        default=os.environ.get("ATTACHMENTS_RES_MODEL", ""),
        help="scope the attachments_ok convenience count to one res_model (test-only; unset in a real drill)",
    )
    parser.add_argument("--out", default=os.environ.get("VALIDATE_OUT"))
    args = parser.parse_args()

    if not args.password:
        print("FATAL: --password/DRILL_PASS is required (never printed)", file=sys.stderr)
        return 1

    result: dict = {
        "ok": False,
        "authenticated": False,
        "health": None,
        "orders": {},
        "sample_attachments": {},
        "filestore": {},
        "egress_blocked": None,
        "errors": [],
    }

    try:
        with httpx.Client(base_url=args.url) as client:
            uid = authenticate(client, args.db, args.user, args.password)
            result["authenticated"] = True
            result["uid"] = uid

            result["health"] = "ok" if check_health(client) else "failed"
            if result["health"] != "ok":
                result["errors"].append("GET /web/health did not return 200")

            actual_orders = rpc_call(client, "sale.order", "search_count", [[]])
            orders_ok = args.expected_orders < 0 or actual_orders == args.expected_orders
            result["orders"] = {
                "expected": args.expected_orders,
                "actual": actual_orders,
                "ok": orders_ok,
            }
            if not orders_ok:
                result["errors"].append(f"sale.order count {actual_orders} != expected {args.expected_orders}")

            # Full filestore listing: every attachment with a store_fname,
            # (id, store_fname, checksum) — reused below both for the "N
            # random" sample and for the exhaustive on-disk check.
            full = rpc_call(
                client,
                "ir.attachment",
                "search_read",
                [[["store_fname", "!=", False]]],
                {"fields": ["store_fname", "checksum"], "limit": 200000, "order": "id asc"},
            )

            # Recent N (by id desc) + N random from the full list, deduped.
            recent = rpc_call(
                client,
                "ir.attachment",
                "search_read",
                [[["store_fname", "!=", False], ["type", "=", "binary"]]],
                {"fields": ["checksum", "file_size"], "limit": args.sample, "order": "id desc"},
            )
            recent_ids = {row["id"] for row in recent}
            pool = [row for row in full if row["id"] not in recent_ids]
            random_sample = random.sample(pool, min(args.sample, len(pool))) if pool else []
            to_download = recent + random_sample

            failed_ids: list[int] = []
            for row in to_download:
                resp = client.get(f"/web/content/{row['id']}?download=true", timeout=60)
                if resp.status_code != 200:
                    failed_ids.append(row["id"])
                    continue
                digest = hashlib.sha1(resp.content).hexdigest()
                if row.get("checksum") and digest != row["checksum"]:
                    failed_ids.append(row["id"])

            result["sample_attachments"] = {
                "checked": len(to_download),
                "ok": len(failed_ids) == 0,
                "failed_ids": failed_ids,
            }
            if failed_ids:
                result["errors"].append(f"attachment sha1 mismatch/download failure: ids={failed_ids}")

            # Exhaustive on-disk filestore check (all rows from `full`).
            root = Path(args.filestore_root)
            missing_ids: list[int] = []
            mismatched_ids: list[int] = []
            for row in full:
                path = filestore_path(root, args.db, row["store_fname"])
                digest = sha1_of_file(path)
                if digest is None:
                    missing_ids.append(row["id"])
                elif row.get("checksum") and digest != row["checksum"]:
                    mismatched_ids.append(row["id"])

            filestore_ok = not missing_ids and not mismatched_ids
            result["filestore"] = {
                "checked": len(full),
                "missing": len(missing_ids),
                "mismatched": len(mismatched_ids),
                "ok": filestore_ok,
                "missing_ids": missing_ids,
                "mismatched_ids": mismatched_ids,
            }
            # Flat convenience count: how many restored attachments are
            # present on disk with a matching sha1. By default this is
            # every attachment in the database (a real restored DB is not
            # limited to one document) — including, for instance, the
            # default company logo, which is a real store_fname-backed
            # attachment nothing in the brief's fixture created. A caller
            # that wants it scoped to attachments of one document (as the
            # local test does, to assert on exactly the 3 it planted) sets
            # --attachments-res-model / ATTACHMENTS_RES_MODEL.
            bad_ids = set(missing_ids) | set(mismatched_ids)
            scope_model = args.attachments_res_model
            if scope_model:
                scoped_ids = {
                    row["id"]
                    for row in rpc_call(
                        client,
                        "ir.attachment",
                        "search_read",
                        [[["store_fname", "!=", False], ["res_model", "=", scope_model]]],
                        {"fields": [], "limit": 200000},
                    )
                }
                result["attachments_ok"] = len([i for i in scoped_ids if i not in bad_ids])
            else:
                result["attachments_ok"] = len(full) - len(bad_ids)
            if not filestore_ok:
                result["errors"].append(f"filestore check: missing={missing_ids} mismatched={mismatched_ids}")
    except (httpx.HTTPError, ValidationError) as exc:
        result["errors"].append(str(exc))

    egress_blocked = check_egress_blocked()
    result["egress_blocked"] = egress_blocked
    if not egress_blocked:
        result["errors"].append("egress probe reached 1.1.1.1:443 — the drill network is not isolated")

    result["ok"] = (
        result["authenticated"]
        and result["health"] == "ok"
        and result["orders"].get("ok", False)
        and result["sample_attachments"].get("ok", False)
        and result["filestore"].get("ok", False)
        and egress_blocked
        and not result["errors"]
    )

    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
