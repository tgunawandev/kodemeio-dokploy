# Offline merchant-of-record candidate (R7 / PAY2)

This is a bounded, provider-neutral local candidate for roadmap R7. It validates
synthetic normalized payment observations and reconciles their exact minor-unit
amounts against caller-supplied orders and payout allocations. It has no network,
database, payment, provider, customer, or secret functionality. Do not use it to
accept payments or make an accounting entry.

## Files

- `CONSTRAINTS.md` records the roadmap/payment conventions reviewed and the
  security and operational boundaries.
- `merchant_event.v1.schema.json` defines the strict normalized event shape.
- `candidate.py` provides the validator, deterministic reconciler, and offline
  JSON command line.
- `examples/reconciliation.synthetic.json` is invented data for local use.
- `tests/test_candidate.py` covers contract, lifecycle, money, dedupe, reversals,
  payout allocations, and the no-I/O boundary.
- `RUNBOOK.md` gives the local procedure and current operational gates.

## Run locally

From the `kodemeio-dokploy` repository root:

```sh
uv run python ops/merchant_of_record/candidate.py \
  ops/merchant_of_record/examples/reconciliation.synthetic.json
uv run pytest ops/merchant_of_record/tests/test_candidate.py -q
```

The sample prints compact JSON with `outcome: clean`, the payment verification
state `unverified`, and exact payout allocation totals. Exit status is `0` for a
valid conflict-free report, `1` when well-formed input has a detected financial
or lifecycle conflict, and `2` for malformed or out-of-bounds input. The input
file is read-only. Use a copied synthetic fixture when exploring changes.

Input is a versioned object containing `contract_version`, `events`, `orders`,
and `payouts`. Each event must validate against
[`merchant_event.v1.schema.json`](merchant_event.v1.schema.json). Unknown keys
are refused. Amounts are integer minor units; no decimal or floating-point money
is accepted. References are opaque identifiers and must not contain customer
data.

## Verification boundary

Every event is synthetic or caller-normalized evidence and remains
`verification.status = unverified`, `verification.adapter = none`. The validator
rejects a `verified` field, a `true` verification boolean, unknown fields, and
any other verification state. This representation is not a webhook receiver,
signature check, or proof that a provider sent an event. No Paddle or Polar
signature rules or adapters are included. Do not add an adapter until the
provider's authoritative current protocol has been reviewed and its design,
secrets handling, replay defenses, and deployment have separate approval.

## Reconciliation policy

Identical repeats of the same `event_id` are counted and ignored. Reusing either
event identity with altered content is a conflict. Lifecycle observations are
sorted by their UTC `occurred_at` time, not file/arrival order, so a delayed older
authorization does not regress a capture. Contradictory terminal histories,
refunds or chargebacks before capture, reversal totals above capture, order
amount/currency mismatches, and payout allocation mismatches produce explicit
conflicts. Any conflict sets top-level `outcome` to `conflict`; consumers must
not treat such output as payment confirmation or settlement approval.

Payout rows must allocate exactly their stated amount. Each allocation must
identify an observed capture and matching order, use the capture's currency,
and stay within that payment's net amount after refunds and chargebacks.
Payments report `allocated_minor` and `unallocated_minor`; an unallocated
balance is visible and is not silently treated as paid out. The utility cannot
establish that provider-supplied order rows, payout rows, or timestamps are true.

### Payout scope and unsupported cases

The supported payout model is deliberately narrow: one currency per payout;
positive allocations of net captured proceeds after recorded refunds and
chargebacks; no fees or other gross-to-net deductions; and allocation sum equal
to the payout amount. It does not model provider gross settlement, fee lines,
FX/conversion, reserves/withholding, adjustments, or negative payout reversals.
There are no fields for those concepts, so unknown provider fields and negative
amounts are refused. A currency difference also fails closed. Do not omit known
fees or adjustments while normalizing a provider report to make it fit this
model; that would manufacture a clean result from incomplete evidence. If any
unsupported item appears, mark that payout unsupported and stop reconciliation
until a separately reviewed contract defines its treatment. The candidate does
not infer Paddle or Polar payout semantics.

The reconciler ties a payout to a payment only when that payment's complete
observed event history is internally consistent. Event ID collisions,
provider event reference collisions, amount/currency mismatches, and conflicting
lifecycle or reversal histories mark the payment history `conflict`; any payout
allocation to it is also `conflict`, never `reconciled`. The top-level result is
`conflict` as well.

This candidate deliberately does not choose the legal seller, tax treatment,
currency conversion policy, payout accounting, or customer rights. Those remain
founder and counsel decisions, listed in `CONSTRAINTS.md` and `RUNBOOK.md`.
