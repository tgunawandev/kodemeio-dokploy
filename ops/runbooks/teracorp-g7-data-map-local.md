# Teracorp G7 / LEG2 — local data-map completeness check

**Local tooling only. Not legal advice, a privacy notice, a verified PIA, a compliance finding, or a go-live approval.** The validator has no network, service, account, Odoo, or secret integration. It never authenticates caller-supplied facts or evidence.

## Product inventory scope

Use the product IDs from the Track B inventory in `kodemeio-docs/superpowers/specs/2026-09-25-teracorp-program-roadmap.md`. The descriptions below are roadmap labels, not claims that these products launched or process data:

| ID | Roadmap product scope |
|---|---|
| B1 | Terakidz free content, printables, guides, monthly kits |
| B2 | Terakidz physical task boxes with QR app activities |
| B3 | Terakidz learning app |
| B4 | Terakidz school/teacher extension |
| B5 | Terakon niche channel portfolio |
| B6 | Terakon templates, prompt packs, playbooks, courses, membership |
| B7 | Terakon Studio SaaS |
| B8 | Terakod opportunity ledger / localized SaaS |
| B9 | Terakod platform kit |
| B10 | Terakod Odoo CE trading package |
| B11 | Terafin education content, templates, courses |
| B12 | Terafin tracking app |

Each product requires its own owner-populated document. Do not copy CW1/KIDO's engineering-specific processing claims into a product map without independently establishing that they apply. The existing `teracorp-cw1-privacy.md` is explicitly a counsel draft and records unresolved retention/consent/deletion/provider questions; Stage A Decisions §4 leaves L1/L2/L4 counsel gates open. This validator does not resolve any of them.

## Inputs and use

- `ops/contracts/teracorp_g7_data_map.v1.schema.json`: strict versioned input structure. A `sections: {}` object is deliberately accepted as an incomplete draft.
- `ops/examples/teracorp_g7_data_map.template.v1.json`: blank structural starter only; it is not a product data map and cannot report complete.
- `ops/examples/teracorp_g7_data_map_complete_unreviewed.synthetic.v1.json`: wholly synthetic demonstration; it is not a model to copy as actual facts.
- `ops/scripts/teracorp_g7_data_map.py`: offline checker. Run, for example:

```sh
uv run python ops/scripts/teracorp_g7_data_map.py path/to/owner-populated-map.json
```

The only states are `incomplete` and `complete-for-counsel-review-unverified`. A successful structural result still sets `verified: false` and `legal_reviewed: false`; it is not a readiness, compliance, legality, or G7-completion signal. Exit status is 0 only for the latter review-packet state; otherwise 1. Error reports do not echo submitted values.

## Required owner work before a review packet can be complete

For one product, establish with provenance: purposes; categories and their classification; processing systems and purpose/category linkage; lawful-basis proposal; consent decision; controller/processor/subprocessor inventory and terms status; actual hosting/residency locations and transfer assessment or an explicit no-transfer assessment; retention triggers/periods/dispositions; access roles; deletion request route and system-by-system steps; a falsifiable deletion test with expected/observed outcome, evidence reference, approved freshness-policy reference and unexpired `valid_until`; and a PIA risk/mitigation/residual-risk list. The validator checks references, exact product scope, unresolved states and expiry, but does not verify the cited evidence. Do not enter personal or customer data in fixtures or test runs.

Any unknown must remain explicitly `unassessed`, `unresolved`, `pending_counsel`, or absent; those states block the review-packet result. Do not replace unknowns with guesses to make the report pass. The proposed basis/consent/retention fields are proposals for founder/counsel scrutiny, not determinations. Counsel review stays `not_started` or `pending`; there is deliberately no reviewed/approved state in this version.

## External gates

Before any live personal/customer data or go-live: founder/product owners must establish the actual per-product processing facts, entity/accountability boundaries, vendor/subprocessor contracts, regions/transfers, approved retention and deletion operations, role access, deletion-test environment and evidence, and operational ownership. Qualified Indonesian counsel must determine applicable lawful basis/consent, notice language, child/sensitive-data protections, cross-border transfer requirements, PIA adequacy, deletion duties and relevant product boundaries. Stage A's explicit L1 (PDP roles, child-related data, consent/deletion), L2 (Terafin OJK/BI boundary), and L4 (Terakod contracts/data segregation) remain external counsel questions. Real-system deletion tests and a founder-controlled review/approval process are not supplied by this tool.
