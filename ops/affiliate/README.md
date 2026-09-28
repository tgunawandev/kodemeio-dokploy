# F9 / AFF1 affiliate factory candidate

This is a bounded, offline candidate under `ops/affiliate/`. It accepts a versioned JSON document,
checks required approval and disclosure fields, checks destination hosts against an exact allowlist,
and can summarize synthetic click and commission events. The checked-in examples are invented test
data. Nothing here contacts Cloudflare, an advertiser or a network, posts to Odoo, or pays money.

Three local components complete the F9 gate ("click logged; commission reconciled") against fakes:

- `scripts/redirect.py` — the short-link handler a Cloudflare Worker would run, as a pure
  request -> response function. `/a/<link_id>` redirects (302, `no-store`) only for an active,
  unexpired, disclosed, allowlisted, attribution-consistent link under approved advertiser and
  terms at request time; everything else is a generic 404 that never echoes the path. Each GET
  logs one `click_event`-shaped record with `visitor_hash` (HMAC of day + /24 or /48 prefix +
  agent class under a >=32-byte secret salt) and `ua_class`; raw IP, user agent, query string and
  referrer are never stored. The hash is pseudonymous, not anonymous: whoever holds the salt can
brute-force a visitor's network prefix, so the salt is a secret with operator-only custody. An optional link `click_id_param` appends only the opaque click id.
  `JsonlClickSink` appends to a 0600 file; `redirect.py serve` is a loopback-only local shim.
- Commission dedupe — one counted commission per `(advertiser_id, lower(order_ref))` (refs are
  accepted as received, e.g. `INV-2026-001` or `123456`); retried postbacks
  are counted once, conflicting economics fail closed; one counted order per click.
- `scripts/statement.py` — reconciles tracked commissions against an
  `affiliate-statement.v1` advertiser statement: matched / missing_from_statement /
  extra_in_statement / amount_mismatch / period_boundary (tracked order dated outside the
  statement's UTC period), totals and difference (exit 0 reconciled, 3 discrepancies,
  1 refused).

Worker placement: no Cloudflare Worker repository exists in the workspace yet, so the portable
reference logic lives here. A Worker port must keep the same refusal set, record shape and
privacy reduction (client address from `CF-Connecting-IP` only, never `X-Forwarded-For`).

`candidate-ready-unverified` means only that this input passed this local structural and semantic
check. It is not approval to publish or operate a program, proof that supplied information is true,
or a legal/compliance conclusion. Approval references and roles are assertions in input and are not
authenticated. A founder must decide whether to authorize any activity and obtain qualified counsel
review for applicable disclosures, advertising/affiliate rules, consumer terms, tax, privacy, and
other obligations. This tool does not encode or decide those rules.

Run the local checks from the Dokploy repository root:

```sh
uv run python ops/affiliate/scripts/affiliate.py validate ops/affiliate/examples/affiliate.synthetic.v1.json
uv run python ops/affiliate/scripts/affiliate.py reconcile ops/affiliate/examples/affiliate.synthetic.v1.json
uv run python ops/affiliate/scripts/statement.py ops/affiliate/examples/affiliate.synthetic.v1.json \
  ops/affiliate/examples/affiliate-statement.synthetic.v1.json
uv run pytest ops/affiliate/tests -q
```

The template is intentionally blocked until an owner supplies real, reviewed facts and a matching
attribution digest. Do not place credentials, personal data, real click logs, or payment data in
these documents. Reconciliation requires every input event to be marked synthetic, deduplicates
identical event IDs, refuses conflicting duplicates, verifies commission arithmetic from the
declared terms, and emits a deterministic evidence summary to stdout. It does not write a ledger.

See [runbook.md](runbook.md) for review gates, evidence interpretation, and work that remains before
any live use could be considered.
