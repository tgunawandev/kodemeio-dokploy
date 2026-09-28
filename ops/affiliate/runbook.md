# F9 / AFF1 offline candidate runbook

## Scope and gate

This is a local data validator and synthetic evidence calculator. It has no network client, browser
pixel, tracking endpoint, redirect handler, persistence layer, scheduler, Cloudflare integration,
API client, Odoo call, or payment operation. Inputs and outputs remain local to the invoking
process. It makes no legal or compliance assertion.

The CLI exits zero only for a structurally and semantically accepted candidate or synthetic report;
it exits nonzero for blocked input. `candidate-ready-unverified` is a software check result, not
founder authorization, advertiser verification, a publication gate, or permission to collect
clicks. `synthetic-evidence-only` is a deterministic calculation over explicitly synthetic rows,
not a payable balance or verified transaction record.

The document's `as_of` is a caller-selected evaluation cutoff used to check approval, terms, link
expiry, and event timestamps. It can describe a historical/backdated snapshot; the tool neither
authenticates it nor compares it with the system clock. Every report requires manual review and
explicitly sets `as_of_authenticated`, `approval_authenticated`, and `consent_authenticated` to
false. Per-event amount ceilings in the schema and the aggregate commission ceiling in Python are
defensive safety bounds only, not founder-approved order, commission, or payout limits.

## Input review

Start with `examples/affiliate.template.v1.json`. Before using non-synthetic values, the owner must
provide and independently review:

- the advertiser identity and a founder approval reference with approver role, approval date, and
  expiry;
- commission model, rate, currency, source terms reference, and effective window approved by the
  parties;
- the exact destination host allowlist and final destination URL for every link;
- a clear per-link disclosure appropriate to its placement and audience, reviewed by qualified
  counsel; and
- the immutable attribution fields (`source`, `medium`, `campaign`, `content`) and the SHA-256 of
  their canonical compact, sorted-key JSON representation. Changing an input requires a new
snapshot digest and review.

The digest is a consistency check, not a signature or tamper-proof audit log: a party who can edit
the document can replace both attribution fields and digest. The output hash provides a stable
reference to the synthetic input rows only.

For each click event, both its attribution fields and digest must exactly match the referenced
link's attribution snapshot. This linkage check does not authenticate the click source.

The validator requires HTTPS, no user information, non-default ports, query, or fragment, and an
exact hostname match. It never fetches the URL, follows redirects, checks DNS/TLS, or verifies that
the destination remains controlled by the named advertiser. The host allowlist check is only a
local string-level guard.

Approval references and status fields are not proof of approval or contractual terms. A valid
shape, unexpired dates, and matching commission math do not establish that an offer exists or that
any claim is accurate. Expired or pending approval/terms, missing disclosure, inactive/expired
links, attribution drift, invalid destinations, non-synthetic events, orphan commission rows, and
commission mismatches block the candidate or reconciliation.

## Offline commands

```sh
uv run python ops/affiliate/scripts/affiliate.py validate ops/affiliate/examples/affiliate.synthetic.v1.json
uv run python ops/affiliate/scripts/affiliate.py reconcile ops/affiliate/examples/affiliate.synthetic.v1.json
uv run pytest ops/affiliate/tests -q
```

Validation prints a small status and stable issue codes; it does not echo untrusted input values.
Reconciliation accepts only `synthetic: true` events. Repeating an identical event ID and identical
content is idempotent and counted as a duplicate. Reusing an event ID with different content fails
closed. Commissions are counted once per `(advertiser_id, order_ref)`: a retried conversion
postback with a new event ID but identical order economics (click, order amount, commission,
currency) is counted once and reported in `duplicate_order_count`; the same `order_ref` with
different economics fails closed as `duplicate_order_ref_conflict`. The attribution policy is
last-click with at most one counted order per click (`multiple_commissions_per_click` blocks).
Commission minor units must equal the declared percentage calculation with half-up integer
rounding; this is a synthetic arithmetic check only. Output includes a digest of the deduplicated
events and `payments_created: false`. No report should be imported as a payable or accounting
record.

## Decisions and work outside this candidate

Before any future live integration, a founder must explicitly choose the business scope, brands,
placement rules, advertiser onboarding and approval authority, event retention, attribution policy,
reversal/refund treatment, and whether this should connect to existing systems. Qualified counsel
must review actual terms and disclosure language and advise on applicable obligations; this
candidate does not claim that any legal requirement is satisfied.

Any production implementation would need separately reviewed integrations and controls for
advertiser/terms source of truth, a consent-aware link or click path if one is authorized, event
integrity and deduplication, reconciliation to trusted order evidence, refunds/reversals,
accounting treatment, access and retention, monitoring, and human approval before any payment.
There is no automatic commission payment in this candidate. No live Cloudflare, API, Odoo, or
external service integration has been implemented or exercised.
