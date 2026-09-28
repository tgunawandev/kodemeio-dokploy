# Runbook — Terakidz product line B1 on kodeme.io (founder-gated)

Takes roadmap row **B1** (Terakidz free content, printables, guides and monthly kits) from
**built-local content** to a live Terakidz line: four free items, a paid monthly kit and a paid
course, sold or given away through the existing factories, and five launch pieces posted by hand.

This is a founder runbook. Read-only checks are **R**; production changes are **M**. Nothing in
this document has been applied by the implementation slice. Every target is the kodeme.io estate,
never idtpp. The line adds **no new Odoo code**: it is data that the F3/F4/F5 factories, the R6
digital lane, the K2 e-book adapter, F8/F10 and PAY1 already consume.

Slice of record: Track B design, 2026-09-28 §4 "B1"
and Track B plan, 2026-09-28 slice TB1. Roadmap row: **B1**.

## What is built locally

| Fact | Evidence |
|---|---|
| Three new printables: `templates/terakidz-routine-chart/`, `templates/terakidz-emotion-cards/`, `templates/terakidz-monthly-kit/` (`edition_month` enum) — wkhtml PDF, Terakidz kit only | commit `77c92ab` |
| One new free guide: `ebooks/terakidz-panduan-aac-rumah/` ("AAC di Rumah", PDF via Typst + EPUB via Pandoc; `book.md` is the canonical derivation) | commit `77c92ab` |
| The line: `product_lines/terakidz.yaml` (`product_line.v1`) | commit `77c92ab` |
| Contract, licence, voice and line-shape tests | `deploys/tests/test_terakidz_line.py`; the line also passes `test_contracts_product_lines.py` |
| All copy is **EXAMPLE CONTENT** (TB-D9), written by Claude in the kit's voice; no medical claim, no promised outcome, no timeline | the header of every file; the voice tests |
| No new asset key: every `asset:` ref is one the F3/F4/F5 sources already use | `test_no_new_asset_key` |
| The local end-to-end run (L1–L10 on `odoo_test_tb_b1`) | **Pending K2** — see "Local e2e" below |

Local evidence is not live evidence. B1 is not operational until every gate below is closed and
recorded in the Track B results document.

## The line

| Item | Kind | Price (IDR) | Publish path | `digital_kind` | Variants |
|---|---|---|---|---|---|
| `terakidz-learning-pack` | template | 0 | `factory_digital` (R6) | `template_pack` | `usia-3-5`, `usia-6-7` |
| `terakidz-routine-chart` | template | 0 | `factory_digital` (R6) | `template_pack` | `pagi`, `malam` |
| `terakidz-emotion-cards` | template | 0 | `factory_digital` (R6) | `template_pack` | `usia-3-5` |
| `terakidz-panduan-aac-rumah` | ebook | 0 | `digital` (K2 `factory_ebook_digital`) | `book` | `orang-tua` |
| `terakidz-monthly-kit` | template | 29 000 | `factory_digital` (R6) | `template_pack` | `edisi-2026-10`, `edisi-2026-11` |
| `terakidz-komunikasi-dasar` | course | 149 000 | `digital` (F5 publisher) | `course` | `default` |

Launch: five F8 briefs, all `publish_mode: creator_handoff` (F10 manual schedule — a human posts
after approval): `routine-chart-launch` and `emotion-cards-launch` (Instagram carousel),
`panduan-aac-launch` (Facebook caption), `monthly-kit-launch` (TikTok caption), `course-launch`
(Instagram caption). Prices are placeholders; the founder sets real ones at G5.

### TB-D8 — how a free item reaches a family

The line file is the same under both answers: a free item is `price_idr: 0` and is still
released and published.

- **(a) zero-price digital product** through the same path as a paid one (R6 records the grant).
  Chosen **if** K2's verify-first **V1** shows a 0-total invoice for a digital product reaches
  `payment_state` `paid`/`in_payment` and fires the `digital_sale` hook.
- **(b) public download** of the same released artefact on the F2 site (`go.terakidz.id`) with
  lead capture. Chosen if V1 does not hold.

Under **(b)** the zero-price digital products the line publishes must not be listed or sold (a
zero-total checkout would never deliver): archive them after publish, or switch the free items to
`publish: none` in a reviewed commit. K2 records which it does in its L8 result.

Record K2's V1 result and the chosen path in the Track B results document before G7.

## Local e2e (K2 harness — not run by TB1)

TB1 ships the content and the line; the harness is kodemeio-odoo slice K2. When K2 is committed:

```bash
# kodemeio-odoo, on its own slice DB only (the harness refuses any other name)
bin/product-line-acceptance terakidz --db odoo_test_tb_b1
```

Pass criteria: L1–L10 (spec §3.3), plus B1's own:

- the two monthly editions (`edisi-2026-10`, `edisi-2026-11`) render with **distinct**
  `input_sha256`;
- the kit's required disclaimer appears in **every** artefact (every source places a
  `{{kit.disclaimers}}` block);
- the four free items are delivered through the TB-D8 path V1 chose;
- the negative variant (the kit's first forbidden phrase, `menyembuhkan autisme`, injected into a
  copy of one source) refuses with the check code `brand_rules`, naming the kit's own phrase
  (as the kernel does) and never the surrounding copy.

Record the result JSON path (`logs/track-b/terakidz-<utc>.json`) and its counts in the TB1 ledger.

## Operational gates (founder)

Every gate is **M** unless marked. Close them in order; G1–G3 unblock everything after them.

### G1 — Name the expert reviewers (special education)

`brands/terakidz.yaml` has `reviewers.expert_logins: []`, so no Terakidz work order can pass the
expert tier on a real instance (the harness uses a synthetic overlay in the test DB only, and
records `experts: synthetic-overlay`). Name at least one special-education reviewer, create the
login (Authentik SSO + the factory expert group, per `factory-template-rollout.md` M2) and add
it to the kit in a reviewed commit.

### G2 — Replace the example copy, expert then founder

Every source and the line say `EXAMPLE CONTENT`. For each of the four new sources and the line:

1. the expert rewrites or approves the copy (voice, no medical claims, the kit's `dont` rules);
2. the founder approves;
3. the commit bumps the source's `version`, removes the `EXAMPLE CONTENT` header line, and
   regenerates `ebooks/terakidz-panduan-aac-rumah/book.md` from `ebook.yaml`;
4. the marker is a test today (`test_committed_line_header_marks_example_content` in
   `test_contracts_product_lines.py`, and the header tests in `test_terakidz_line.py`). Removing it
   is a reviewed change to those rules in the same commit — replace the marker with the approval
   record — and `uv run pytest deploys/tests -q` stays green.

**The monthly kit's two editions differ only in their month label today** (template.v1 has no
per-month content). A real edition needs month-specific activities: either one template per month
or a version bump that changes the blocks as well as adding the `edition_month` value. A new enum
value alone relabels the same kit. If any AI-assisted copy survives into a real version, place
`{{kit.ai_disclosure}}` in the source too.

A release on a real instance still needs expert then founder approval inside Odoo; approving copy
in git does not release anything.

### G3 — Register the assets with licence evidence

The line uses only existing keys: `asset:font-nunito` (OFL-1.1), `asset:image-terakidz-book-logo`
(the guide's cover), and — through the learning pack and the course — `asset:image-terakidz-learning-hero`
and `asset:footage-terakidz-intro`. Register and verify each on the target instance with its
licence evidence, per `factory-template-rollout.md` M3, `ebook-factory-rollout.md` M3 and
`course-factory-rollout.md` M3. A new image for any B1 source is a new asset: register it with
licence evidence **first**, record it here, then reference it.

### G4 — Typst and Pandoc in the image

The guide renders its PDF through Typst and its EPUB through Pandoc; without them the render
refuses by name. Follow `ebook-factory-rollout.md` M1b (the two image gates). The printables use
wkhtmltopdf, already in the fleet image.

### G5 — Seller of record (R2) and prices

R6 does not choose the legal entity, journal, tax or merchant contract. Decide per
`digital-delivery-rollout.md` M0 (the Terakidz company as seller of record), then set the real
prices of the monthly kit and the course (the line's 29 000 / 149 000 are placeholders).

### G6 — Live PAY1

Paid items settle through PAY1 (Midtrans). Follow `midtrans-rollout.md` stages 0–4
(sandbox rehearsal before production keys). Refunds stay human (R6 revokes on refund).

### G7 — F2 site listing (`go.terakidz.id`)

List the line on the Terakidz landing site per `factory-website-terakidz.md` (the domain does not
exist yet; M0 there decides the instance). Under TB-D8 (b), the free items' download and lead
capture live here too.

### G8 — Launch (F8 → F10, manual)

Run the five briefs through F8 per `content-factory-rollout.md` M5 on one order first. Each piece
needs its gate (expert then founder); F10 creates a publication only after approval, and
`creator_handoff` means a human posts it. Launch no piece before G2 and G7.

## Rollback

- **Content:** revert the TB1 commit(s) in kodemeio-dokploy; nothing on an instance changes until
  a source is imported there.
- **On an instance:** archive the `digital.product` rows the line created (never delete a product
  with a paid line), and refund per R6 so grants are revoked. Archive the imported templates and
  the book by import (a new version), not by deleting them.
- **Launch:** withdraw the F10 publication by hand on the channel; the handoff mode never posted
  anything itself.

## Not in this runbook (deliberate)

- The harness itself and `factory_ebook_digital` (kodemeio-odoo slice K2).
- B2 task boxes, B3 app, B4 school extension (their own slices).
- Any real child data: the printables ask for none and the harness uses synthetic `.test` buyers.
