# Teracorp G7 / LEG2 — privacy inventory, deletion test and per-product documents (local)

**Local tooling only.** Nothing here is legal advice, a compliance result, proof that an inventory
is exhaustive, proof that evidence is authentic, or proof that production deletion works. The
checked-in examples are synthetic. No tool in this runbook opens a network connection.

G7 has **one** validator: `ops/scripts/teracorp_privacy_inventory.py` (schema
`ops/contracts/teracorp_privacy_inventory.v2.schema.json`). The former B1–B12 free-text data-map
validator `teracorp_g7_data_map.py` is a deprecated alias that warns on stderr and delegates to it;
its v1 format and the privacy-inventory v1 format are retired.

## Tools

| Step | Tool | Output |
|---|---|---|
| 1. Inventory | owner-maintained JSON (schema v2) | products, processors, purposes, stores, flows, retention rules, evidence refs |
| 2. Deletion test | `ops/scripts/teracorp_g7_erasure.py` | one hashed (optionally HMAC-signed) evidence record per product |
| 3. Gate | `ops/scripts/teracorp_privacy_inventory.py` | exit 0 = structurally complete; 1 = `incomplete`; 2 = invalid |
| 4. Documents | `ops/scripts/teracorp_g7_documents.py` | per product: `data-map.md`, `processors.md`, `retention.md`, `pia-skeleton.md` |

## Inventory rules

- Every identifier is a typed generated opaque token: `prod_`, `proc_`, `ev_`, `purp_`, `flow_`,
  `store_` (and `key_` for a signing key) followed by 32 lowercase hex characters. Generate with
  `python3 -c 'import uuid; print("store_" + uuid.uuid4().hex)'`. Keep the mapping from opaque ID to
  product name or vendor in the founder's access-controlled register, never in the inventory.
- No personal data, names, contact details, raw events, free text, URLs or secrets. Values are
  closed vocabularies (service kind, hosting region, purpose kind, store kind, category, trigger,
  disposition) or integers.
- Each product lists its **stores** (`odoo_orm`, `chatwoot_contact`, `supabase_row`,
  `backup_snapshot`, `other`) with the processor hosting them, the categories held and an erasure
  mode: `erase` (must be empty after erasure) or `retention_bound` (immutable backup: needs a
  digest-only tombstone and must expire within `retention_days`).
- Each held category needs a retention rule with a period; missing or `null` periods are unresolved.
- Unknown / unresolved / pending / asserted states stay unresolved. `store_kind: other`, an
  `unknown` hosting region, and any deletion test that is not `passed` with a matching fresh record
  make the package `incomplete` and the CLI exit 1.

## Running locally (synthetic example)

```sh
# 2. deletion test against the in-process fakes (Odoo ORM, Chatwoot API, SQLite "Supabase", backups)
uv run python ops/scripts/teracorp_g7_erasure.py --as-of 2026-09-28 --freshness-days 90 \
  --out /tmp/g7-evidence ops/examples/teracorp_privacy_inventory.synthetic.v2.json

# 3. gate (the checked-in records are bound to the example by sha256)
uv run python ops/scripts/teracorp_privacy_inventory.py --as-of 2026-09-28 --allow-local-fake-evidence \
  --deletion-record ops/examples/teracorp_g7_deletion_evidence.synthetic-a.v1.json \
  --deletion-record ops/examples/teracorp_g7_deletion_evidence.synthetic-b.v1.json \
  ops/examples/teracorp_privacy_inventory.synthetic.v2.json

# 4. documents
uv run python ops/scripts/teracorp_g7_documents.py --as-of 2026-09-28 --allow-local-fake-evidence \
  --deletion-record ops/examples/teracorp_g7_deletion_evidence.synthetic-a.v1.json \
  --deletion-record ops/examples/teracorp_g7_deletion_evidence.synthetic-b.v1.json \
  --out /tmp/g7-docs ops/examples/teracorp_privacy_inventory.synthetic.v2.json
```

After a harness run, set the product's deletion evidence ref `sha256` to the printed
`record_sha256`, `deletion_tested_on` to the run date and `deletion_test_status` to the record's
status. The gate then checks, per product: record digest matches the inventory ref, same product,
same date, `valid_until >= as_of`, the record's `store_map_sha256` equals the product's current store
map (so adding or changing a store invalidates the test), every store is covered with `absent` or
`tombstoned` with matching store kind and erasure mode, and — with `--hmac-key-file` — a valid
HMAC-SHA256 signature. Records from any environment other than `local_fake` are unresolved
(`unsigned_or_unverified`) unless a key is supplied and the signature verifies. Records are bounded
to a 366-day validity window, may not pass an `erase` store by tombstone, and may not report residue
on a passing store (`ops/contracts/teracorp_g7_deletion_evidence.v1.schema.json`). A `failed` record, a
missing record, or a `local_fake` record without `--allow-local-fake-evidence` is unresolved.
`--freshness-days` must come from an owner-approved policy; the tools invent no default.

## Adapter interface

`teracorp_g7_erasure.ErasureAdapter` has `seed`, `observable` (a seed that cannot be observed fails
the store, so an empty store cannot pass vacuously), `erase` (the product's erasure step for that
store) and `verify` (independent read path returning an outcome and residue count). The backup fake also
simulates a restore and fails as `residue` if the tombstone does not suppress the subject. Adapters are
chosen by `store_kind`; a store with no adapter fails as `no_adapter`. Only the in-process fakes in
`FAKE_ADAPTERS` exist. Adapters against live or disposable copies of real systems must be written
and reviewed separately before use; never point this harness at production data.

## Product scope reference (roadmap Track B)

Real inventories use opaque product IDs; the founder's register maps them to: B1 Terakidz free
content/printables, B2 Terakidz task boxes + QR app, B3 Terakidz learning app, B4 Terakidz
school/teacher, B5 Terakon niche channels, B6 Terakon templates/courses/membership, B7 Terakon
Studio SaaS, B8 Terakod opportunity ledger / localized SaaS, B9 Terakod platform kit, B10 Terakod
Odoo CE trading package, B11 Terafin education content, B12 Terafin tracking app. Do not copy CW1's
engineering data map (`teracorp-cw1-privacy.md`) into another product without establishing it applies.

## Operational gates (not done by this tooling)

Founder/product owners: real per-product inventories, stores, processors and terms, regions and
transfers, approved retention periods and freshness policy, signing-key custody. Engineering:
reviewed adapters for real systems and deletion drills against isolated copies, then production
drills. Qualified Indonesian counsel: lawful basis, consent/notice, child and payment data,
transfers, PIA adequacy, deletion duties (Stage A L1/L2/L4 remain open). Do not wire these tools
to JARVIS/Hatchet without a separate approved design.
