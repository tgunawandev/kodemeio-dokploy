# Local runbook: R7 / PAY2 candidate

Status: local candidate only. No provider account is connected and this
procedure processes synthetic data only. It is not an operational payment
runbook.

## Local check

1. Work from a clean copy of `examples/reconciliation.synthetic.json`; keep all
   identifiers invented and avoid customer or account data.
2. Run the candidate and tests from the repository root:

   ```sh
   uv run python ops/merchant_of_record/candidate.py \
     ops/merchant_of_record/examples/reconciliation.synthetic.json
   uv run pytest ops/merchant_of_record/tests/test_candidate.py -q
   ```

3. Confirm the report is `clean`, verification is `unverified`, the order and
   captured amounts/currencies match exactly, and payout allocations total the
   payout amount. A clean result is only arithmetic over supplied synthetic
   records; it is not provider verification or a ledger posting instruction.
4. For a conflict, preserve the synthetic input and output for debugging. Do
   not manually edit a conflict report into a clean result. Correct the fixture
   inputs and rerun. A malformed input exits `2`; a well-formed conflict exits
   `1`; a clean report exits `0`.

## Fail-closed conflict codes

| Code | Meaning | Candidate response |
|---|---|---|
| `event_id_collision`, `provider_event_ref_collision` | An event identity was reused with changed content | Refuse to infer which observation is genuine |
| `payment_order_conflict`, `unknown_order` | Payment does not map to exactly one supplied order | Keep payment unresolved |
| `amount_mismatch`, `currency_mismatch` | Capture/auth or reversal differs from exact expected money | Do not treat as paid |
| `lifecycle_conflict` | Incompatible terminal states or repeated capture | Keep lifecycle conflicted |
| `reversal_before_capture`, `reversal_exceeds_capture` | Refund/chargeback has no earlier capture or exceeds it cumulatively | Do not infer a valid net payment |
| `payout_allocation_mismatch` | Payout allocation sum differs from payout amount | Keep payout unreconciled |
| `payout_unmatched_payment`, `payout_currency_mismatch`, `payout_exceeds_net_capture` | Allocation lacks matching captured proceeds or over-allocates them | Keep payout unreconciled |
| `payout_conflicted_payment_history` | Allocation points at an event history with any internal conflict | Keep payout unreconciled; resolve the payment evidence first |

## Payout scope

The candidate accepts only a fee-free, single-currency payout whose positive
order-level allocations sum exactly to its positive payout amount. Allocations
are bounded by net captured proceeds after normalized refunds and chargebacks.
This is not a provider gross-settlement model and does not account for provider
fees, FX, reserves, withholding, adjustments, or negative payout reversals.
Those cases are unsupported: unknown payout/allocations keys, non-positive
allocation amounts, and currency mismatches fail closed. Never drop fee or
adjustment rows during normalization to force a match. Mark such a provider
payout unsupported until the provider's actual semantics have been reviewed and
a separate versioned contract is approved. No Paddle or Polar behavior is
assumed here.

A conflict anywhere in a payment's event history marks that payment history as
conflicted. Any payout allocation to that payment is then marked `conflict` and
emits `payout_conflicted_payment_history`; the payout cannot show as
`reconciled` from conflicting evidence.

## Gates before any real-provider work

The following are intentionally unresolved, and this candidate does not clear
them:

- Founder and counsel approval of the legal seller/merchant-of-record model,
  jurisdictions, consumer terms, tax/VAT, invoicing, refund and chargeback
  liabilities, privacy/retention obligations, and required disclosures.
- Founder decision on the supported legal entities, provider account owner,
  payout bank and accounting owner, settlement currency, fees, FX treatment,
  and reconciliation source of truth.
- Provider account onboarding and written confirmation of its current webhook
  authenticity rules and payout data format. Paddle and Polar are alternatives,
  not interchangeable protocols; neither is implemented here.
- Separate threat model and review for signature validation, replay protection,
  key custody/rotation, event persistence, access control, and incident response.
- Accounting and security acceptance of the end-to-end implementation and a
  provider-approved sandbox rehearsal before any production decision.

Until those gates are completed in a separately authorized change, do not add
credentials, call a provider, receive live webhooks, create or refund payments,
use real customer data, or deploy this directory as a service.
