# Factory performance evidence rollout (F0f / FC6)

Status: local implementation in acceptance; **do not install in production until the gates below
are confirmed**. This adapter is advisory/read-only and does not deploy an analytics provider, add
tracking, publish content, change products, or modify factory gates.

## Current source inventory

| Measure | Local source | Attribution | Rollout state |
|---|---|---|---|
| Views | `content.publication.metric.snapshot` | Existing F10 publication → released F8 piece → one explicit `asset:` reference; ambiguous links remain unattributed | Local adapter only; no production run |
| Watch time | Same publication metric snapshot | Same as views; seconds | Local adapter only; no production run |
| Clicks | None verified | None | Explicitly unavailable |
| Paid sales | `factory.digital.purchase` | Company + bound digital product + UTC day; aggregate count only | Local adapter; no production run |
| Course completions | None verified | None | Explicitly unavailable |
| Subscription churn | None verified | None | Explicitly unavailable |

The publication framework stores point observations, not a provider reporting interval. The adapter
keeps that point timestamp and its source-row reference; do not relabel it as a daily/monthly window.
Paid sales are grouped by product and UTC day from confirmed-paid purchase ledger rows; no partner,
invoice, or sale-line identifier is retained. Only normalized, allow-listed metrics are exported.
Provider payloads, customer identifiers, URLs, credentials and raw events are not copied. Measured
zero, missing observation, stale evidence, unattributed evidence and unavailable sources are
separate states.

## Founder decisions required before a production install

1. Confirm the authorised users/companies for `Factory Performance Manager` and the data-retention
   period for immutable aggregates.
2. Confirm legal/privacy basis for using publication aggregates in opportunity research.
3. Decide whether to approve any future analytics provider (for example Cloudflare Web Analytics),
   read-only credential scope and its retention. No provider or credential is configured by this
   runbook.
4. Confirm the R6 paid-line ledger as the sales count source (gross confirmed sales, including lines
   later refunded) and approve its aggregate retention. Confirm completion/churn sources only when
   those products exist. Until then, keep those metrics `unavailable`.
5. Confirm the publishing group and its manual-first mode are installed and independently accepted.

Do not record secret values in this runbook or expose them to an agent/browser.

## Installation and verification (founder-operated)

1. Review the code, F0f acceptance results and these source limits. Confirm all founder decisions
   above in the change record.
2. On the designated kodeme.io Odoo estate only, explicitly select the `performance` group from
   `install/private-factory.yaml` (it depends on `publishing`). Do not add it to a default profile.
3. Assign `Factory Performance Manager` only to approved named Odoo users with the least set of
   allowed companies. Keep automation agents and ordinary operators outside this group.
4. In a controlled synthetic acceptance transaction, import one existing publication metric
   snapshot and one paid digital purchase window. Verify the company/brand/asset/product mapping,
   the source-row link, a measured-zero case, and an ambiguous-attribution case. Verify no raw
   provider keys or buyer/invoice/line identifiers appear in the research export or logs.
5. Verify a second-company user cannot read or import the first company's observation. Verify that
   source gaps show clicks/sales/completions/churn as unavailable and absent publication values as
   missing—not zero.
6. Verify corrections append a linked row and the original remains unchanged. Verify no UI/API path
   for this group can write/delete observations, alter a factory gate, publish, or spend.
7. Record the install change, named operators, chosen retention policy and evidence link. Production
   is not accepted until monitoring/backup requirements for the host are satisfied by Wave 0.

## Failure and rollback

- On unexpected scope, mapping or privacy behaviour, remove the manager-group assignment and stop
  imports. Preserve existing observations as audit evidence; do not delete or rewrite them.
- Correct an erroneous numeric source revision only by appending a reviewed correction linked to
  its prior observation. Keep the correction reason in the change record, not in free-form metric
  data.
- Disable any future scheduled importer at its owning integration. Do not disable company record
  rules or broaden access as a workaround.
- After a code fix, rerun the disposable acceptance and neighbour suites before resuming imports.

## Local acceptance commands

```sh
kctl-odoo local test factory_performance -d odoo_test_factory_performance --tags /factory_performance
bin/validate-bundles install/
bin/lint-addon-i18n src/private/factory/factory_performance
```

The local result is not production evidence. No production host, DNS, credentials, external provider,
deployment, or push is authorized by this runbook.
