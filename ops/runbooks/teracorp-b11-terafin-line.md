# Runbook — Terafin product line B11 (financial education, no advice), founder-gated

Takes the Terafin line from **built-local** (committed sources, contract-tested, run end to end on a
disposable test DB by the product-line harness) to **sold on kodeme.io**: a real brand, copy written
and signed off by finance educators, a legal boundary opinion, real prices, licence-clean assets, a
seller of record and live payments.

Everything here is a founder step (**M**) or a counsel step (**L**) except the read-only checks, which
are marked as such. Nothing here has been applied. The estate is kodeme.io only (`-p kodemeio`,
`./odoo.sh kod … prod`); no idtpp host, bucket, key, job or agent is touched. No production write
happens without `--yes`.

Slice of record: `kodemeio-docs/superpowers/specs/2026-09-28-teracorp-track-b-design.md` (B11, §3.1–3.3,
TB-D8/TB-D9) and `…/plans/2026-09-28-teracorp-track-b.md` (slice TB11). Roadmap row: **B11**. The
Terafin tracking app is B12 (slices TB12 + ENT), not this runbook.

## The law this line lives under: education, never advice

Terafin content **explains**; it never **recommends**. Concretely, no Terafin artefact:

- names a financial instrument, issuer, provider or product;
- tells the reader to buy, sell, hold or join anything;
- promises or implies a return, a profit or the absence of risk.

The line enforces this as data, with no kernel change:

- the kit `brands/terafin.yaml` forbids 12 claim-shaped phrases (`rekomendasi saham` first; also
  `pasti untung`, `sinyal trading`, `beli sekarang`, `dijamin untung`, `bebas risiko`,
  `tanpa risiko`, …), which `factory.check.brand_rules` refuses **by name** at check time. Bare
  `dijamin` is deliberately allowed, because honest education says "tidak dijamin";
- the kit requires two disclaimers on **every** artefact: `Konten edukasi, bukan nasihat keuangan
  atau ajakan berinvestasi.` and the `kit uji coba` (trial kit) line;
- `deploys/tests/test_terafin_line.py` holds these sources to a stricter authoring lint: no
  instrument names, no return vocabulary and no buy/sell calls. Carry that list into the real kit
  (G1).

## What the line is (committed, read-only)

`product_lines/terafin.yaml` (contract `product_line.v1`), every item under the `terafin` kit:

| Item | Source | Factory | Digital kind | Publish path | Placeholder price (IDR) |
|---|---|---|---|---|---|
| Monthly budget planner (2 variants: Oktober/November) | `templates/terafin-budget-planner` | F3 xlsx | `planner` | `factory_digital` | **0 (free)** |
| Weekly cash-flow worksheet (2 variants: household / micro business) | `templates/terafin-cashflow-worksheet` | F3 PDF (wkhtml) | `template_pack` | `factory_digital` | **0 (free)** |
| E-book "Dasar Keuangan Keluarga" (PDF + EPUB, 5 chapters) | `ebooks/terafin-dasar-keuangan-keluarga` | F4 | `book` | `digital` via K2 `factory_ebook_digital` | 59 000 |
| Course "Literasi Keuangan 101" (3 modules, 5 lessons, 8 quiz questions, no video) | `courses/terafin-literasi-keuangan-101` | F5 html | `course` | `digital` (course publisher) | 149 000 |

The free items are delivered through the TB-D8 path K2 records: a zero-price product through R6 if
K2's verify-first check V1 holds (a 0-total invoice reaches `paid` and fires the `digital_sale`
hook), else a public download on the F2 site. No F2 site is built for Terafin in built-local.

Launch: three F8 briefs, all `creator_handoff` through F10 (nothing publishes directly):

| brief_key | Channel / shape | Intended copy (EXAMPLE CONTENT, no advice) |
|---|---|---|
| `terafin-planner-launch` | Instagram carousel | The free monthly budget sheet: plan and record spending every week. |
| `terafin-ebook-launch` | TikTok caption | The e-book's five short chapters: cash flow, budget, emergency fund, calm debt handling. |
| `terafin-course-launch` | Facebook caption | The course: reading family cash flow, building a budget, judging an offer calmly. |

The exact synthetic launch copy the dokploy tests hold to the kit is `F8_PIECES` in
`deploys/tests/test_terafin_line.py`. product_line.v1 carries only the opaque `brief_key`, so K2
decides where the brief copy comes from. Whatever it generates must carry both kit disclaimers and
the channel AI disclosure (`Konten ini dibuat dengan bantuan kecerdasan buatan (AI).`).

🔴 **Every string in these sources is `EXAMPLE CONTENT`** (TB-D9). The kit is a `draft` PLACEHOLDER
whose required disclaimer contains `kit uji coba`. Every source places `{{kit.disclaimers}}`: each
template, each workbook sheet, the first and last chapters of the e-book, and every course reading
and slide deck. So `brand_rules` refuses any Terafin render that lacks the education or trial-kit
notice, and an accidental release cannot pass for real Terafin product.

Read-only proof (any time): `uv run pytest deploys/tests/test_terafin_line.py deploys/tests/test_contracts_product_lines.py -q`.

## Local end-to-end (built-local bar, no founder step): what K2 must run

This runs on a disposable DB after K1 (committed, `0af75e3`) and K2 (the harness plus
`factory_ebook_digital`, both in kodemeio-odoo) land. Until K2 lands, B11 is **content-ready, not
built-local**.

```bash
cd kodemeio-odoo
bin/teracorp-product-line terafin --db odoo_test_tb_b11
```

It must pass L1–L10 (spec §3.3). For this line specifically:

- **Setup (test DB only).**
  - Import `brands/terafin.yaml` through `factory_base`, with the synthetic expert/founder overlay.
    The kit has `expert_logins: []`, so the result must record `experts: synthetic-overlay`.
  - Register synthetic verified assets for `asset:font-inter` (font, OFL-1.1) and
    `asset:image-terafin-book-logo` (image, the e-book cover logo). The logo key is a placeholder
    with no bytes; the renderers draw a labelled placeholder for it.
- **L1.** All 4 items import with their committed sha, and a mutated source refuses by name.
- **L2.** There are 6 renders: 2 per template plus the book and the course. Each artefact's text
  contains **both** `kit uji coba` and `Konten edukasi, bukan nasihat keuangan atau ajakan
  berinvestasi.`
  - The two variants of each template have distinct `input_sha256`.
  - Each course lesson deck carries the disclaimers on its own.
- **L3 — the advice refusal (B11 extra).** The harness takes a copy of each source with
  `rekomendasi saham` injected. It is the kit's **first** forbidden phrase, so the harness's generic
  negative variant already uses it. Each copy must refuse at check time with a named
  `brand_rules` result (`forbidden phrase 'rekomendasi saham'`) that never echoes the copy. Run it
  for **each** of:
  1. a template (at least one; both is better: xlsx and PDF);
  2. the e-book;
  3. the course;
  4. one F8 launch piece.
- **L4.** Release needs the expert, then the founder: two distinct synthetic users. Direct writes
  and agent writes refuse.
- **L5.** Each item gives exactly one sellable product, and a republish is idempotent.
- **L6 / L7.** The paid lines are the e-book and the course. Each gets exactly one grant through the
  PAY1 fake and signer, and a replayed settlement creates nothing new. The delivered sha equals the
  released sha, and a refund revokes the grant.
- **L8.** Both free templates are delivered through the TB-D8 path K2 recorded.
- **L9.** There are three launch pieces. Each passes its gate, and no F10 publication exists before
  approval.
- **L10.** No PII appears in logs or the result JSON: grep them against the synthetic buyer
  `buyer@example.test`.

Record the result JSON path (`logs/track-b/terafin-<utc>.json`) and counts in the TB11 ledger
(`kodemeio-docs/.superpowers/sdd/2026-09-28-teracorp-track-b-tb11/progress.md`).

---

## G1 — A real Terafin brand kit (M)

The committed `brands/terafin.yaml` is a test fixture: `status: draft`, every invented value is
`PLACEHOLDER`, and `expert_logins: []`. Before anything is sold:

1. Commission and approve the Terafin brief: voice, audience, palette, and fonts with licence
   evidence.
2. Replace every `PLACEHOLDER` value and set `status: active`.
3. Name real `reviewers.expert_logins`. These are **finance educators** (see G2), never
   salespeople of any financial product.
4. Replace the `kit uji coba` disclaimer with the approved Terafin wording. **Keep** the education
   disclaimer, or counsel's replacement for it (G3).
5. Keep and extend the forbidden phrases, and fold in TB11's authoring lint (instrument names,
   return vocabulary, buy/sell calls from `test_terafin_line.py`). Keep bare `dijamin` allowed
   unless counsel says otherwise.
6. `test_placeholder_kits.py` refuses an `active` kit that still carries `PLACEHOLDER`, and pins
   `terakidz` as the only active kit today. Update that pin **in the same commit** as the kit.
7. Import the kit on the instance through `factory_base`, using the kit-import step in
   `ops/runbooks/factory-template-rollout.md`.

## G2 — Finance educators as named experts; real copy (M)

- Recruit at least one **finance educator** as the expert reviewer, for example a certified
  financial-literacy trainer or educator. They are recorded as `expert_logins` on the kit. They
  rewrite and sign off every source: workbook blocks, worksheet, e-book chapters, course readings,
  decks and quizzes. Every quiz keeps its explanation.
- The educator must have **no commercial tie** to any financial product the copy could be read as
  promoting. Record that declaration with the approval.
- The founder approves after the expert: expert, then founder, through the tier flow.
- After approval:
  - remove the `EXAMPLE CONTENT` headers only from files whose copy is approved. The line-file
    header test requires the marker while the line is example content; change that rule
    deliberately, never by deleting the marker;
  - bump each changed source's `version` (the importer refuses a lower or equal version);
  - regenerate `ebooks/terafin-dasar-keuangan-keluarga/book.md` from `ebook.yaml`. It is derived,
    and the tests fail on drift.

## G3 — L2 counsel: the OJK/BI boundary (L)

Before any Terafin artefact is sold or published, get a written opinion from Indonesian counsel on
the boundary between **financial education** and regulated activity, covering OJK (financial
services, including investment advice and marketing of financial products) and BI (payment
systems). The opinion should answer:

1. Does any sold Terafin artefact, as written, amount to investment advice, a financial-product
   offer, or the marketing of one? Which wording would?
2. Is the education disclaimer sufficient, and in which placement? Or what replaces it?
3. What must the educator's declaration of independence contain?
4. Do the course quiz and the e-book's worked examples (illustrative proportions, an
   emergency-fund rule of thumb) need extra framing?
5. Is selling education products to households and micro-business owners subject to any
   consumer-protection rule we must reflect in the checkout or refund copy?

Record the opinion's reference, not its contents, in the founder's access-controlled register. Any
wording change it requires goes through G1/G2. The B12 tracking app (no custody, no trading) needs
its own counsel pass; it is not covered here.

## G4 — Assets with licence evidence (M)

| Asset key | Kind | Licence evidence needed | Used by |
|---|---|---|---|
| `asset:font-inter` | font | OFL-1.1 (the kit's font already; re-verify if G1 changes the fonts) | every item |
| `asset:image-terafin-book-logo` | image | owned or stock licence. **Placeholder key:** no bytes are committed. Rename it in `ebook.yaml` to the real logo's key when G1 lands. | e-book cover |

Register and verify each one as a usable `factory.asset` on the instance before importing any
source. A font or image that is not verified, commercial and unexpired refuses by name.

## G5 — Renderers in the image (M)

- Budget planner: `openpyxl` (already present; F3 proved it).
- Cash-flow worksheet: wkhtmltopdf (the fleet's PDF engine).
- E-book: **Typst** for the PDF and **Pandoc** for the EPUB. If either is missing from the image,
  it refuses with its own named error. A single-format release needs an explicit recorded choice on
  the work order (`ops/runbooks/ebook-factory-rollout.md`).
- Course: the `html` renderer needs nothing (`ops/runbooks/course-factory-rollout.md`).

## G6 — Pricing and free delivery (M)

The prices in `product_lines/terafin.yaml` are placeholders. Set real prices (integer IDR), commit,
and re-run the harness locally before touching the instance.

For the two free templates, confirm the TB-D8 outcome K2 recorded:

- **(a)** zero-price through R6 (the grant is tracked); or
- **(b)** a public download on an F2 Terafin site with lead capture. That needs a Terafin site,
  which is a separate founder decision.

## G7 — Seller of record and live payments (M)

- **R2 seller of record.** R2 is still open on the program roadmap. Decide the entity (Odoo
  company) that sells Terafin education products and its tax treatment, per
  `ops/runbooks/digital-delivery-rollout.md` M0. R6 refuses a cross-company checkout, so every
  Terafin product must live in that one company.
- **Live PAY1.** Switch the Midtrans provider from the local fake to production keys, per
  `ops/runbooks/teracorp-midtrans-rollout.md`. The harness only ever uses the PAY1 fake and signer.

## G8 — Release and publish on the instance (M)

For each item, go through public actions only, never a direct write:

1. import;
2. render per variant;
3. submit;
4. checks;
5. **expert (finance educator) approve, then founder approve**;
6. release;
7. publish on the item's path: the `factory_digital` binding for the two templates,
   `factory_ebook_digital` for the e-book, and the `digital` course publisher for the course.

Each item gives one sellable product, and a republish is idempotent.

## G9 — Launch (M)

1. Create the three F8 briefs named in the line (`terafin-planner-launch`, `terafin-ebook-launch`,
   `terafin-course-launch`).
2. Pass their gate. The kit's forbidden phrases and disclaimers apply to every piece.
3. Approve.
4. Schedule each through F10 as `creator_handoff` (`ops/runbooks/content-factory-rollout.md`,
   `ops/runbooks/publishing-rollout.md`).

An F10 publication exists only after approval. Never boost or target a launch piece with wording
that the kit would refuse in the piece itself.
