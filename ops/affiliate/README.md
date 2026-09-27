# F9 / AFF1 affiliate factory candidate

This is a bounded, offline candidate under `ops/affiliate/`. It accepts a versioned JSON document,
checks required approval and disclosure fields, checks destination hosts against an exact allowlist,
and can summarize synthetic click and commission events. The checked-in example is invented test
data. Nothing here tracks a visitor, creates a link redirect, contacts an advertiser, or pays money.

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
uv run pytest ops/affiliate/tests -q
```

The template is intentionally blocked until an owner supplies real, reviewed facts and a matching
attribution digest. Do not place credentials, personal data, real click logs, or payment data in
these documents. Reconciliation requires every input event to be marked synthetic, deduplicates
identical event IDs, refuses conflicting duplicates, verifies commission arithmetic from the
declared terms, and emits a deterministic evidence summary to stdout. It does not write a ledger.

See [runbook.md](runbook.md) for review gates, evidence interpretation, and work that remains before
any live use could be considered.
