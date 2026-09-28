# R7 / PAY2 merchant-of-record candidate constraints

This directory is an isolated, local-only candidate for roadmap R7 / PAY2. The
roadmap (program roadmap, 2026-09-25,
row R7) describes foreign-market SaaS payments through a merchant of record
(Paddle/Polar), with webhook and payout reconciliation tests; its account gate
is still open. PAY1 results and `ops/runbooks/midtrans-rollout.md`
provide nearby payment conventions, but PAY1 is a different provider and is not
a signature or protocol specification for this candidate.

## Contract and processing limits

- Keep every artifact under this new `ops/merchant_of_record/` path. Do not
  change shared roadmaps, registries, existing contracts, tests, or runbooks.
- Use a strict, explicitly versioned normalized event contract. Unknown fields,
  malformed identifiers, invalid currency/amount values, and ambiguous money
  movements fail closed.
- JSON/normalized event input always reports verification state as
  `unverified`, with no verification adapter. No input field, including a
  caller-supplied boolean, can assert cryptographic verification. The only path
  to `verified` is the provider-neutral `WebhookVerifier` seam: HMAC-SHA256 over
  `<unix-seconds>.<exact raw body>`, constant-time comparison, a bounded
  timestamp tolerance, and replay rejection by `provider_event_ref`. It returns
  a `VerifiedEvent` that callers cannot construct themselves. Tests use an
  invented synthetic secret only; no real signing secret is stored or read.
- Implement deterministic duplicate handling, order-independent replay of
  lifecycle events, exact minor-unit and currency checks, refunds, chargebacks,
  and payout allocation reconciliation. Conflicting histories produce explicit
  conflict outcomes and never a payable/settled claim.
- Payout scope is limited to positive, same-currency net-proceeds allocations
  that exactly equal the payout amount. Fees, FX, reserves, withholding, other
  adjustments, and negative payout reversals are unsupported and must be
  refused or explicitly marked unsupported; do not infer provider payout
  semantics. A payout allocation tied to any conflicted event history cannot
  be labeled reconciled.
- Keep both per-payout and aggregate allocation counts bounded; direct callers
  of the Python API must not bypass the CLI input-size guard to trigger
  unbounded reconciliation work.
- This is offline arithmetic over caller-normalized synthetic data. It must not
  perform network, database, provider, customer, payment, or secret operations.
- Reference syntax cannot establish that a value is free of personal data;
  names and phone-like strings can pass and are echoed in reports. Inputs must
  be invented synthetic values only. A future provider adapter must add a
  separately reviewed, non-PII identifier/tokenization boundary before any
  real source data is accepted.
- Provider-specific Paddle/Polar signature header formats and adapters are
  deliberately absent; they would map provider headers onto the neutral seam. Any such adapter remains blocked pending authoritative provider
  specifications, review, and a separately approved implementation.

## Operational gates (not satisfied by this candidate)

Before any real provider work, the founder and counsel must decide and document
the legal seller/merchant-of-record arrangement, supported entities and
jurisdictions, tax/VAT and invoicing treatment, consumer/refund/chargeback
responsibilities, data/privacy retention, provider account ownership, payout
bank/accounting ownership, and settlement currency/accounting policy. Provider
account approval and verified signature requirements are also outstanding.
Until those decisions and later security/accounting reviews are complete, this
candidate is not deployable and must not process real customers or funds.
