# R7 / PAY2 merchant-of-record candidate constraints

This directory is an isolated, local-only candidate for roadmap R7 / PAY2. The
roadmap (`kodemeio-docs/superpowers/specs/2026-09-25-teracorp-program-roadmap.md`,
row R7) describes foreign-market SaaS payments through a merchant of record
(Paddle/Polar), with webhook and payout reconciliation tests; its account gate
is still open. PAY1 results and `ops/runbooks/teracorp-midtrans-rollout.md`
provide nearby payment conventions, but PAY1 is a different provider and is not
a signature or protocol specification for this candidate.

## Contract and processing limits

- Keep every artifact under this new `ops/merchant_of_record/` path. Do not
  change shared roadmaps, registries, existing contracts, tests, or runbooks.
- Use a strict, explicitly versioned normalized event contract. Unknown fields,
  malformed identifiers, invalid currency/amount values, and ambiguous money
  movements fail closed.
- Synthetic webhook observations always report verification state as
  `unverified`, with no verification adapter. No input field, including a
  caller-supplied boolean, can assert cryptographic verification.
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
- Provider-specific Paddle/Polar signature rules and adapters are deliberately
  absent. Any such adapter remains blocked pending authoritative provider
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
