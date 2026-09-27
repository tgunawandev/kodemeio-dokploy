# Teracorp G7 / LEG2 — Local privacy inventory

This tool validates only the structure and internal references of a founder/counsel-prepared
inventory. It is not legal advice, a compliance result, proof that the inventory is exhaustive,
proof that evidence is authentic, or proof that deletion succeeded. The checked-in example is
synthetic and is not Teracorp product evidence.

Do not enter personal data, customer/child records, names, contact details, raw events, free-form
notes, URLs, or secrets. Use opaque lowercase identifiers and keep source evidence in its approved,
access-controlled location. Digests are caller-supplied and are not authenticated by the checker.
`none_declared`, `not_applicable_asserted`, and `not_required_asserted` are preserved as unverified
assertions; they are not legal conclusions. Unknown or missing inventory is never represented as
zero.

Run from the Dokploy repository root:

```sh
uv run python ops/scripts/teracorp_privacy_inventory.py \
  --as-of 2026-09-28 ops/examples/teracorp_privacy_inventory.synthetic.v1.json
```

An exit code of 0 means only that package shape and local references validated. Output always has
`verified: false`, `legal_reviewed: false`, and `deletion_verified: false`. Real inventory scope,
purpose descriptions, consent, retention/deletion policy, PIA decisions, and deletion drills must
be supplied and reviewed by the founder and qualified counsel. No production system or deletion
endpoint is called. Do not wire this tool to JARVIS/Hatchet without a separate approved design.
