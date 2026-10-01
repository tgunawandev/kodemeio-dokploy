---
record: terakod-selection
schema: selection-record (informal, v1)
as_of_date: 2026-09-28
ledger: terakod-ledger.v1.json
selected_opportunity_ref: opp-terakod-content-studio
selected_product: Terakona Studio
decision: TB-D2
owner_ref: owner-founder
owner_review_authenticated: false
status: proposed
---

# Terakode first-SaaS selection record

> **EXAMPLE CONTENT — not approved product copy.** Every candidate in the ledger is
> synthetic (`.test` sources, `Synthetic candidate:` claims). Nothing here is market
> evidence, and nothing here promotes a candidate.

## What is selected

The founder-default **TB-D2** names **Terakona Studio** as B8's first SaaS: the ledger
candidate `opp-terakod-content-studio` (a brand-kit content studio for small creator
teams, billed by QRIS or virtual account). B7 builds it on the TB8 plumbing modules.

## Why this candidate (per the Track B design, not per the evidence)

- It is the only SaaS the blueprint already names, so choosing it removes one whole build.
- It dogfoods the content factories (F8) and the placeholder Terakona kit.
- The plumbing (auth, tenant, billing via the entitlement bridge, WhatsApp, support
  handoff) is product-agnostic, so a later switch to another candidate costs only B7.

## Other candidates kept in the ledger

| Ref | Candidate | State |
|---|---|---|
| `opp-terakod-invoice-reminders` | chat-based invoice reminders for small traders | unreviewed, pending |
| `opp-terakod-clinic-booking` | appointment booking with e-wallet deposits | unreviewed, pending |

## What this record is not

- `owner_review_authenticated: false`: the FC2 validator treats `owner_review` as
  caller-supplied metadata, never an authenticated attestation, and so does this record.
- Every source assertion stays `unverified`. Replacing the synthetic evidence with real
  market evidence, the founder's authenticated review and the actual choice are the
  **operational gate** of B8.
