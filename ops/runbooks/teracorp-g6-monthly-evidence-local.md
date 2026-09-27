# Teracorp G6 / LEG1 — offline monthly evidence inventory

Local evidence-inventory tooling only. It is not legal advice, a compliance finding, a legal
approval, or operational JARVIS functionality. G6 remains founder- and qualified-counsel-gated.

The version-1 package covers one month and opaque evidence references for six counsel decision
domains: PDP/data protection, OJK/regulatory boundary, Terakod contract/seller-of-record review,
processor/data-map changes, access/retention/deletion, and incidents/exceptions/remediation. Each
decision must name counsel, include opaque qualification and evidence references, and carry issue,
effective, and expiry timestamps. Product/processor change entries and incident entries must link
to a counsel decision covering the exact scope. Unknown, unresolved, uncovered, future, expired,
or unhandled items block completeness.

The checker cannot authenticate the counsel identity or qualifications, verify cited evidence,
establish that inventories are exhaustive, or make a legal determination. It does not encode legal
rules, classifications, interpretations, advice, or freshness thresholds. No output is `compliant`,
approved, or actionable, and the package is not connected to JARVIS or Hatchet.

Use only a founder/counsel-prepared, access-controlled evidence package; keep evidence itself
outside this report and put opaque references in the JSON. Example (the checked-in fixture is
synthetic and is not a model for actual facts):

```sh
uv run python ops/scripts/teracorp_g6_monthly_evidence.py \
  --as-of 2026-09-28T00:00:00Z path/to/monthly-package.json
```

`--as-of` is required so output is deterministic. Exit code 0 means only
`complete-for-counsel-review-unverified`; exit code 1 means `incomplete`. Neither state approves
business activity. A founder and qualified counsel must conduct and document the real monthly
review outside this tool.
