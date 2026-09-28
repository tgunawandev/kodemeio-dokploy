# G4 — offline monthly budget reconciliation

This read-only utility compares an explicitly supplied monthly budget with invoice amounts allocated
to named legal entities. It performs deterministic arithmetic only; it is not a ledger, accounting
system, tax calculation, payment tool, or financial recommendation.

```bash
python3 ops/scripts/budget_reconcile.py \
  ops/metrics/examples/monthly-budget.synthetic.json
```

## Input contract

- One canonical `YYYY-MM` period, one uppercase three-letter report currency, 1–50 entity budget
  rows, and up to 2,000 invoice rows.
- All amounts must be decimal strings in the root report currency. Normalize any source-currency
  invoice amount before entry, using the founder-approved rate/policy outside this tool; this CLI
  does not convert currencies or choose rates.
- Each invoice date must fall inside the declared month. Each invoice has one or more positive
  allocations to distinct entities in the budget, and those allocations must sum exactly to the
  invoice amount. There is no implicit unallocated or overhead bucket.
- Decimal values are limited to 64 characters; the input JSON file is limited to 1 MiB. Duplicate
  JSON keys, unknown fields, duplicate invoice/entity IDs, URL-like evidence refs, negative amounts,
  and allocations to unbudgeted entities fail closed.
- `invoice_ref` is only an opaque shape-checked reference. The tool does not authenticate it,
  verify the underlying invoice, or prove that the supplied invoice set is complete. An exact
  arithmetic reconciliation is not source verification or accounting approval.

The checked-in example is entirely synthetic. Its entity labels, period, amounts, and references
must not be treated as the real budget. Keep customer names, credentials, invoice contents,
and free-form descriptions out of the input. The CLI reads only the explicit file, prints JSON to
stdout, and does not persist or transmit information.

G4 remains non-operational until the founder approves dated budgets and currency/FX policy per
entity, records actual invoices against source evidence, reviews the allocations, and reconciles
the result to the accounting system.

Local checks: `uv run pytest -q deploys/tests/test_budget_reconcile.py`.
