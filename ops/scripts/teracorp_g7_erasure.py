#!/usr/bin/env python3
"""Teracorp G7 per-product deletion-test harness (local fakes / disposable stores only).

For every product in a v2 privacy inventory, this harness seeds one synthetic data subject into
every store the product's data map lists, runs the product's erasure routine through a pluggable
per-store adapter, verifies the subject is absent from every live store (or, for retention-bound
backups, that a digest-only tombstone exists and the backup expires within its retention bound),
and emits a hashed (optionally HMAC-signed) evidence record consumed by
``teracorp_privacy_inventory.py``.

Only the in-process fakes in ``FAKE_ADAPTERS`` ship here. Adapters for live Odoo, Chatwoot,
Supabase or backup storage are an operational gate and must be reviewed separately; this module
never opens a network connection. Synthetic identifiers never leave the process: records carry a
digest of the subject reference only.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teracorp_privacy_inventory as inventory  # noqa: E402

MAX_FRESHNESS_DAYS = inventory.MAX_FRESHNESS_DAYS

# --------------------------------------------------------------------------- core types


@dataclass(frozen=True)
class SyntheticSubject:
    """A generated data subject; its identifiers exist only inside the harness process."""

    ref: str
    name: str
    email: str
    phone: str

    @classmethod
    def generate(cls) -> SyntheticSubject:
        token = secrets.token_hex(12)
        return cls(
            ref=f"subj_{token}",
            name=f"Synthetic Subject {token}",
            email=f"subj-{token}@example.invalid",
            phone=f"+0000{int(token[:10], 16) % 10**10:010d}",
        )

    @property
    def identifiers(self) -> tuple[str, ...]:
        return (self.ref, self.name, self.email, self.phone)

    @property
    def digest(self) -> str:
        return inventory.sha256_hex(self.ref.encode("ascii"))

    def mentioned_in(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        lowered = value.lower()
        return any(identifier.lower() in lowered for identifier in self.identifiers)


@dataclass(frozen=True)
class StoreSpec:
    store_id: str
    store_kind: str
    erasure_mode: str
    retention_days: int | None


@dataclass(frozen=True)
class StoreOutcome:
    store_id: str
    store_kind: str
    erasure_mode: str
    outcome: str
    residue_count: int

    def as_record(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "store_kind": self.store_kind,
            "erasure_mode": self.erasure_mode,
            "outcome": self.outcome,
            "residue_count": self.residue_count,
        }


class ErasureAdapter(ABC):
    """One store's part of a product erasure routine.

    ``seed`` plants the synthetic subject, ``observable`` proves the seed is visible (so an empty
    store cannot pass vacuously), ``erase`` performs the product's erasure step, and ``verify``
    returns ``(outcome, residue_count)`` using an independent read path.
    """

    def __init__(self, spec: StoreSpec) -> None:
        self.spec = spec

    @abstractmethod
    def seed(self, subject: SyntheticSubject, as_of: date) -> None: ...

    @abstractmethod
    def observable(self, subject: SyntheticSubject) -> bool: ...

    @abstractmethod
    def erase(self, subject: SyntheticSubject, as_of: date) -> None: ...

    @abstractmethod
    def verify(self, subject: SyntheticSubject, as_of: date) -> tuple[str, int]: ...


class LiveStoreAdapter(ErasureAdapter):
    """A store that must hold no trace of the subject after erasure."""

    @abstractmethod
    def residue(self, subject: SyntheticSubject) -> int: ...

    def observable(self, subject: SyntheticSubject) -> bool:
        return self.residue(subject) > 0

    def verify(self, subject: SyntheticSubject, as_of: date) -> tuple[str, int]:
        count = self.residue(subject)
        return ("absent" if count == 0 else "residue", count)


AdapterFactory = Callable[[StoreSpec], ErasureAdapter]

# --------------------------------------------------------------------------- fake Odoo ORM


class FakeOdooEnv:
    """Minimal in-memory stand-in for an Odoo ORM environment (create/search/read/write/unlink)."""

    MODELS = ("res.partner", "sale.order", "mail.message")

    def __init__(self) -> None:
        self._rows: dict[str, dict[int, dict[str, Any]]] = {model: {} for model in self.MODELS}
        self._next_id = 1

    def create(self, model: str, values: dict[str, Any]) -> int:
        record_id = self._next_id
        self._next_id += 1
        self._rows[model][record_id] = dict(values)
        return record_id

    def search(self, model: str, field: str, value: Any) -> list[int]:
        return [record_id for record_id, row in self._rows[model].items() if row.get(field) == value]

    def read(self, model: str, record_id: int) -> dict[str, Any]:
        return dict(self._rows[model][record_id])

    def write(self, model: str, record_ids: list[int], values: dict[str, Any]) -> None:
        for record_id in record_ids:
            self._rows[model][record_id].update(values)

    def unlink(self, model: str, record_ids: list[int]) -> None:
        for record_id in record_ids:
            self._rows[model].pop(record_id, None)

    def all_values(self) -> list[Any]:
        return [value for rows in self._rows.values() for row in rows.values() for value in row.values()]


class FakeOdooAdapter(LiveStoreAdapter):
    """Odoo partner + sale order + chatter. Orders are kept (accounting) but de-identified."""

    def __init__(self, spec: StoreSpec, env: FakeOdooEnv | None = None) -> None:
        super().__init__(spec)
        self.env = env or FakeOdooEnv()

    def seed(self, subject: SyntheticSubject, as_of: date) -> None:
        partner = self.env.create(
            "res.partner", {"name": subject.name, "email": subject.email, "phone": subject.phone, "active": True}
        )
        order = self.env.create(
            "sale.order", {"partner_id": partner, "client_order_ref": subject.ref, "note": f"Deliver to {subject.name}"}
        )
        self.env.create("mail.message", {"res_id": order, "model": "sale.order", "body": f"Contact {subject.email}"})

    def erase(self, subject: SyntheticSubject, as_of: date) -> None:
        anonymous = f"Anonymised {subject.digest[:12]}"
        for partner in self.env.search("res.partner", "email", subject.email):
            orders = self.env.search("sale.order", "partner_id", partner)
            self.env.write("sale.order", orders, {"client_order_ref": False, "note": False})
            for order in orders:
                self.env.unlink("mail.message", self.env.search("mail.message", "res_id", order))
            self.env.write("res.partner", [partner], {"name": anonymous, "email": False, "phone": False})

    def residue(self, subject: SyntheticSubject) -> int:
        return sum(1 for value in self.env.all_values() if subject.mentioned_in(value))


# --------------------------------------------------------------------------- fake Chatwoot API


class FakeChatwootApi:
    """In-memory Chatwoot-like REST surface; deleting a contact cascades its conversations."""

    def __init__(self) -> None:
        self.contacts: dict[int, dict[str, Any]] = {}
        self.conversations: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    def _id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        parts = path.strip("/").split("/")
        if method == "POST" and parts == ["contacts"]:
            contact_id = self._id()
            self.contacts[contact_id] = dict(body or {})
            return {"id": contact_id}
        if method == "POST" and parts == ["conversations"]:
            conversation_id = self._id()
            self.conversations[conversation_id] = {
                "contact_id": (body or {})["contact_id"],
                "messages": list((body or {}).get("messages", [])),
            }
            return {"id": conversation_id}
        if method == "GET" and parts == ["contacts", "search"]:
            query = str((body or {}).get("q", "")).lower()
            return [
                {"id": contact_id, **contact}
                for contact_id, contact in self.contacts.items()
                if any(query and query in str(value).lower() for value in contact.values())
            ]
        if method == "GET" and parts == ["conversations"]:
            return [{"id": key, **value} for key, value in self.conversations.items()]
        if method == "DELETE" and len(parts) == 2 and parts[0] == "contacts":
            contact_id = int(parts[1])
            self.contacts.pop(contact_id, None)
            for key in [key for key, value in self.conversations.items() if value["contact_id"] == contact_id]:
                self.conversations.pop(key)
            return None
        raise ValueError("unsupported fake Chatwoot request")


class FakeChatwootAdapter(LiveStoreAdapter):
    def __init__(self, spec: StoreSpec, api: FakeChatwootApi | None = None) -> None:
        super().__init__(spec)
        self.api = api or FakeChatwootApi()

    def seed(self, subject: SyntheticSubject, as_of: date) -> None:
        contact = self.api.request(
            "POST", "contacts", {"name": subject.name, "email": subject.email, "phone_number": subject.phone}
        )
        self.api.request(
            "POST", "conversations", {"contact_id": contact["id"], "messages": [f"Hi, I am {subject.name}"]}
        )

    def erase(self, subject: SyntheticSubject, as_of: date) -> None:
        for contact in self.api.request("GET", "contacts/search", {"q": subject.email}):
            self.api.request("DELETE", f"contacts/{contact['id']}")

    def residue(self, subject: SyntheticSubject) -> int:
        count = 0
        for identifier in subject.identifiers:
            count += len(self.api.request("GET", "contacts/search", {"q": identifier}))
        for conversation in self.api.request("GET", "conversations"):
            count += sum(1 for message in conversation["messages"] if subject.mentioned_in(message))
        return count


# --------------------------------------------------------------------------- disposable SQLite "Supabase"


class SqliteSupabaseAdapter(LiveStoreAdapter):
    """Supabase-shaped rows in a disposable in-memory SQLite database (parameterized SQL only)."""

    TABLES = {
        "auth_users": ("id", "email", "phone"),
        "profiles": ("user_id", "display_name"),
        "events": ("user_id", "payload"),
    }

    def __init__(self, spec: StoreSpec, connection: sqlite3.Connection | None = None) -> None:
        super().__init__(spec)
        self.db = connection or sqlite3.connect(":memory:")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS auth_users (id TEXT PRIMARY KEY, email TEXT, phone TEXT);"
            "CREATE TABLE IF NOT EXISTS profiles (user_id TEXT, display_name TEXT);"
            "CREATE TABLE IF NOT EXISTS events (user_id TEXT, payload TEXT);"
        )

    def seed(self, subject: SyntheticSubject, as_of: date) -> None:
        self.db.execute("INSERT INTO auth_users VALUES (?, ?, ?)", (subject.ref, subject.email, subject.phone))
        self.db.execute("INSERT INTO profiles VALUES (?, ?)", (subject.ref, subject.name))
        self.db.execute("INSERT INTO events VALUES (?, ?)", (subject.ref, json.dumps({"signup": subject.email})))
        self.db.commit()

    def erase(self, subject: SyntheticSubject, as_of: date) -> None:
        user_ids = [row[0] for row in self.db.execute("SELECT id FROM auth_users WHERE email = ?", (subject.email,))]
        for user_id in user_ids:
            self.db.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))
        self.db.commit()

    def residue(self, subject: SyntheticSubject) -> int:
        count = 0
        for table, columns in self.TABLES.items():
            for column in columns:
                for identifier in subject.identifiers:
                    # Table/column names come from the fixed TABLES constant, never from input.
                    query = f"SELECT COUNT(*) FROM {table} WHERE instr(lower({column}), lower(?)) > 0"  # noqa: S608
                    count += self.db.execute(query, (identifier,)).fetchone()[0]
        return count


# --------------------------------------------------------------------------- fake retention-bound backups


class FakeBackupAdapter(ErasureAdapter):
    """Immutable backup snapshots: erasure records a digest-only tombstone instead of rewriting.

    Passing requires (a) a tombstone keyed by the subject digest with no raw identifier, so a
    restore can re-apply erasure, and (b) every snapshot holding the subject expiring no later
    than the tombstone date plus the store's declared retention bound.
    """

    def __init__(self, spec: StoreSpec) -> None:
        super().__init__(spec)
        self.snapshots: list[dict[str, Any]] = []
        self.tombstones: list[dict[str, Any]] = []
        self.snapshot_lifetime_days = spec.retention_days or 0

    def seed(self, subject: SyntheticSubject, as_of: date) -> None:
        self.snapshots.append(
            {
                "taken_on": as_of,
                "expires_on": as_of + timedelta(days=self.snapshot_lifetime_days),
                "rows": [{"subject_digest": subject.digest, "email": subject.email, "name": subject.name}],
            }
        )

    def _holding(self, subject: SyntheticSubject) -> list[dict[str, Any]]:
        return [
            snapshot
            for snapshot in self.snapshots
            if any(subject.mentioned_in(value) for row in snapshot["rows"] for value in row.values())
        ]

    def observable(self, subject: SyntheticSubject) -> bool:
        return bool(self._holding(subject))

    def erase(self, subject: SyntheticSubject, as_of: date) -> None:
        self.tombstones.append({"subject_digest": subject.digest, "recorded_on": as_of, "suppress_on_restore": True})

    def restore(self) -> list[dict[str, Any]]:
        """Simulate a restore: rows are re-applied, then tombstones suppress erased subjects."""
        suppressed = {item["subject_digest"] for item in self.tombstones if item.get("suppress_on_restore") is True}
        return [
            row for snapshot in self.snapshots for row in snapshot["rows"] if row["subject_digest"] not in suppressed
        ]

    def verify(self, subject: SyntheticSubject, as_of: date) -> tuple[str, int]:
        holding = self._holding(subject)
        tombstones = [item for item in self.tombstones if item["subject_digest"] == subject.digest]
        leaked = sum(1 for item in self.tombstones for value in item.values() if subject.mentioned_in(value))
        if leaked:
            return ("residue", leaked)
        if holding and not tombstones:
            return ("tombstone_missing", len(holding))
        resurrected = sum(1 for row in self.restore() if any(subject.mentioned_in(value) for value in row.values()))
        if resurrected:
            return ("residue", resurrected)
        if self.spec.retention_days is None:
            return ("retention_exceeded", len(holding))
        recorded = min((item["recorded_on"] for item in tombstones), default=as_of)
        bound = recorded + timedelta(days=self.spec.retention_days)
        overdue = sum(1 for snapshot in holding if snapshot["expires_on"] > bound)
        if overdue:
            return ("retention_exceeded", overdue)
        return ("tombstoned", 0)


FAKE_ADAPTERS: dict[str, AdapterFactory] = {
    "odoo_orm": FakeOdooAdapter,
    "chatwoot_contact": FakeChatwootAdapter,
    "supabase_row": SqliteSupabaseAdapter,
    "backup_snapshot": FakeBackupAdapter,
}

# --------------------------------------------------------------------------- harness


def _spec(store: Mapping[str, Any]) -> StoreSpec:
    return StoreSpec(store["store_id"], store["store_kind"], store["erasure_mode"], store["retention_days"])


def run_deletion_test(
    product_id: str,
    stores: list[dict[str, Any]],
    adapters: Mapping[str, AdapterFactory],
    *,
    as_of: date,
    freshness_days: int,
    environment: str = "local_fake",
    subject: SyntheticSubject | None = None,
    hmac_key: bytes | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    """Seed, erase and verify one synthetic subject across every listed store; return a record."""
    if not 1 <= freshness_days <= MAX_FRESHNESS_DAYS:
        raise inventory.InputError("freshness_days must be an owner-approved value in 1..366")
    if environment not in inventory.RECORD_ENVIRONMENTS:
        raise inventory.InputError("environment is invalid")
    if not stores:
        raise inventory.InputError("a deletion test requires at least one data-map store")
    if (hmac_key is None) != (key_id is None):
        raise inventory.InputError("hmac key and key_id must be supplied together")
    subject = subject or SyntheticSubject.generate()
    specs = [_spec(store) for store in stores]
    outcomes: dict[str, StoreOutcome] = {}
    active: list[tuple[StoreSpec, ErasureAdapter]] = []

    def settle(spec: StoreSpec, outcome: str, residue: int) -> None:
        outcomes[spec.store_id] = StoreOutcome(spec.store_id, spec.store_kind, spec.erasure_mode, outcome, residue)

    for spec in specs:
        factory = adapters.get(spec.store_kind)
        if factory is None:
            settle(spec, "no_adapter", 0)
            continue
        try:
            adapter = factory(spec)
            adapter.seed(subject, as_of)
            if not adapter.observable(subject):
                settle(spec, "seed_not_observable", 0)
                continue
        except Exception:  # noqa: BLE001 - any adapter fault must fail the test, never crash it
            settle(spec, "erasure_error", 0)
            continue
        active.append((spec, adapter))
    for spec, adapter in active:  # the product erasure routine, store by store
        try:
            adapter.erase(subject, as_of)
        except Exception:  # noqa: BLE001
            settle(spec, "erasure_error", 0)
    for spec, adapter in active:
        if spec.store_id in outcomes:
            continue
        try:
            outcome, residue = adapter.verify(subject, as_of)
        except Exception:  # noqa: BLE001
            outcome, residue = "erasure_error", 0
        if spec.erasure_mode == "erase" and outcome in {"tombstoned", "tombstone_missing", "retention_exceeded"}:
            outcome = "residue"  # an erase-mode store must be empty; retention semantics do not apply
        settle(spec, outcome, residue)

    ordered = [outcomes[spec.store_id].as_record() for spec in sorted(specs, key=lambda item: item.store_id)]
    passed = all(item["outcome"] in inventory.PASSING_OUTCOMES for item in ordered)
    body = {
        "record_version": 1,
        "kind": inventory.RECORD_KIND,
        "product_id": product_id,
        "executed_on": as_of.isoformat(),
        "valid_until": (as_of + timedelta(days=freshness_days)).isoformat(),
        "environment": environment,
        "status": "passed" if passed else "failed",
        "subject_digest": subject.digest,
        "store_map_sha256": inventory.store_map_digest(stores),
        "stores": ordered,
    }
    signature = None
    if hmac_key is not None:
        signature = {"alg": "hmac-sha256", "key_id": key_id, "value": inventory.sign_body(body, hmac_key)}
    record = {"body": body, "signature": signature}
    inventory.parse_record(record)  # self-check: never emit a record the validator would refuse
    return record


def run_inventory(
    payload: Any,
    as_of: str,
    adapters: Mapping[str, AdapterFactory],
    *,
    freshness_days: int,
    hmac_key: bytes | None = None,
    key_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run the deletion test for every product with a store map; structural faults raise."""
    view = inventory.inspect(payload, as_of)
    return [
        run_deletion_test(
            product.product_id,
            product.stores,
            adapters,
            as_of=view.as_of,
            freshness_days=freshness_days,
            hmac_key=hmac_key,
            key_id=key_id,
        )
        for product in view.products
        if product.stores
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", required=True, help="explicit YYYY-MM-DD execution date")
    parser.add_argument("--freshness-days", required=True, type=int, help="owner-approved validity window (1..366)")
    parser.add_argument("--out", required=True, type=Path, help="directory for evidence records")
    parser.add_argument("--hmac-key-file", type=Path, help="sign records with HMAC-SHA256")
    parser.add_argument("--key-id", help="opaque key_<32 hex> identifier for the signing key")
    parser.add_argument("input", type=Path, help="explicit local JSON inventory (schema v2)")
    args = parser.parse_args(argv)
    try:
        key = inventory.load_key(args.hmac_key_file)
        if args.key_id is not None:
            inventory._typed_id(args.key_id, "key", "key_id")
        records = run_inventory(
            inventory.load(args.input),
            args.as_of,
            FAKE_ADAPTERS,
            freshness_days=args.freshness_days,
            hmac_key=key,
            key_id=args.key_id,
        )
    except inventory.InputError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 2
    summary = []
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        paths = []
        for record in records:
            path = args.out / f"{record['body']['product_id']}.deletion-evidence.json"
            path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            paths.append(path)
    except OSError:
        print(json.dumps({"status": "invalid", "error": "filesystem_error"}))
        return 2
    for record, path in zip(records, paths, strict=True):
        body = record["body"]
        summary.append(
            {
                "product_id": body["product_id"],
                "status": body["status"],
                "environment": body["environment"],
                "record_sha256": inventory.record_body_digest(body),
                "path": str(path),
            }
        )
    failed = [item for item in summary if item["status"] != "passed"]
    print(json.dumps({"status": "failed" if failed else "passed", "records": summary}, sort_keys=True))
    return 1 if failed or not summary else 0


if __name__ == "__main__":
    sys.exit(main())
