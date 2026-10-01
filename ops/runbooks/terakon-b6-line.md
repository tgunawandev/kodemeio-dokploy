# Runbook — Terakona product line B6 (content part), founder-gated

Takes the Terakona line from **built-local** (committed sources, contract-tested, and — once the K2
product-line harness lands — run end to end on a disposable test DB) to **sold on kodeme.io**: real brand, real copy,
real prices, licence-clean assets, a seller of record and live payments.

Everything here is a founder step (**M**) except the read-only checks, which are marked as such.
Nothing here has been applied. The estate is kodeme.io only (`-p kodemeio`, `./odoo.sh kod … prod`);
no idtpp host, bucket, key, job or agent is touched. No production write happens without `--yes`.

Slice of record: Track B design, 2026-09-28 (B6, §3.1–3.3,
TB-D5/TB-D9) and Track B plan, 2026-09-28 (slice TB6). Roadmap row: **B6**. The B6 row
closes only when this line **and** the membership slice **MEM** (`factory_membership`, kodemeio-odoo)
are both built-local; membership is not in this line file and needs no step here.

## What the line is (committed, read-only)

`product_lines/terakon.yaml` (contract `product_line.v1`), every item under the `terakon` kit:

| Item | Source | Factory | Digital kind | Publish path | Placeholder price (IDR) |
|---|---|---|---|---|---|
| Weekly planner (2 variants: week 40/41) | `templates/terakon-planner` (F3, pre-existing) | F3 xlsx | `planner` | `factory_digital` | 29 000 |
| Content calendar (2 variants: Oct/Nov 2026) | `templates/terakon-content-calendar` | F3 xlsx | `template_pack` | `factory_digital` | 39 000 |
| Prompt pack (2 variants: two niches) | `templates/terakon-prompt-pack` | F3 xlsx | `prompt_pack` | `factory_digital` | 49 000 |
| Playbook e-book (PDF + EPUB) | `ebooks/terakon-playbook-channel-niche` | F4 | `book` (no `playbook` code exists) | `digital` via K2 `factory_ebook_digital` | 79 000 |
| Course (3 modules, 2 quizzes, no video) | `courses/terakon-channel-niche-101` | F5 html | `course` | `digital` (course publisher) | 149 000 |

Launch: three F8 briefs — Instagram carousel (prompt pack), TikTok script (playbook), YouTube script
(course) — all `creator_handoff` through F10. Nothing publishes directly.

🔴 **Every string in these sources is `EXAMPLE CONTENT`** (TB-D9), and the kit is a `draft` PLACEHOLDER
whose required disclaimer contains `kit uji coba`. Every source places `{{kit.disclaimers}}` (each
workbook's sidecar places the disclaimer block; the playbook in its first and last chapter; every
course reading and deck), so `factory.check.brand_rules` **refuses** any render under the placeholder
kit that lacks the trial-kit notice. That refusal is the guard: an accidental release cannot pass for
real Terakona product.

Read-only proof (any time): `uv run pytest deploys/tests/test_terakon_line.py deploys/tests/test_contracts_product_lines.py -q`.

## Local end-to-end (built-local bar, no founder step)

Runs on a disposable DB after K1 (committed) and K2 (the harness, kodemeio-odoo) land:

```bash
cd kodemeio-odoo
bin/product-line-acceptance terakon --db odoo_test_tb_b6
```

It must pass L1–L10 (spec §3.3) and, for this line specifically:

- **L2**: all 5 items × their variants render under `terakon`; each artefact's text contains
  `kit uji coba`; the two variants of each workbook have distinct `input_sha256`.
- **L3**: the negative variant injects the kit's first forbidden phrase (`dijamin untung`) into a
  copy of one source and gets a named `brand_rules` refusal that never echoes the text.
- **L8** is **not exercised by this line** (no free item). L9: three briefs, and no F10 publication
  exists before approval.
- The harness must register, **in the test DB only**, synthetic verified assets for
  `asset:font-inter` (font, OFL) and `asset:image-terakon-cover-placeholder` (image) — the kit's
  experts list is empty, so the harness's synthetic expert/founder overlay applies and the result
  records `experts: synthetic-overlay`.

Record the result JSON path (`logs/track-b/terakon-<utc>.json`) and counts in the TB6 ledger.

---

## G1 — A real Terakona brand kit (M)

The committed `brands/terakon.yaml` is a test fixture (`status: draft`, every value `PLACEHOLDER`,
`expert_logins: []`). Before anything is sold:

1. Commission and approve the Terakona brief (voice, audience, palette, fonts with licence evidence).
2. Replace every `PLACEHOLDER` value; set `status: active`; name real `reviewers.expert_logins`.
3. Replace the `kit uji coba` disclaimer with the approved Terakona disclaimer(s). The dokploy test
   `test_placeholder_kits.py` refuses an `active` kit that still carries `PLACEHOLDER`, and pins
   `terakidz` as the only active kit today — update that pin **in the same commit** as the kit.
4. Decide the forbidden phrases. The draft forbids only `dijamin untung` and `bebas risiko`; the
   TB6 copy is additionally held to a no-outcome-claim list (`CLAIM_PHRASES` in
   `deploys/tests/test_terakon_line.py`). A course about running a channel is where
   guaranteed-growth or guaranteed-income claims creep in — carry that list into the real kit.
5. Import the kit on the instance through `factory_base` (the F3 runbook's kit-import step,
   `ops/runbooks/factory-template-rollout.md`).

## G2 — Real copy, reviewed (M)

Experts rewrite each source (workbook blocks, prompt table, playbook chapters, course readings,
decks and quizzes) and the founder approves it. Then:

- remove the `EXAMPLE CONTENT` headers only from files whose copy is approved (the line-file header
  test in `test_contracts_product_lines.py` requires the marker on EVERY line file, and
  `test_terakon_line.py` on every TB6 YAML source — change those tests deliberately in the same
  commit, never by deleting the marker alone);
- bump each changed source's `version` (the importer refuses a lower or equal version);
- regenerate `ebooks/terakon-playbook-channel-niche/book.md` from `ebook.yaml` (it is derived; the
  test fails on drift);
- the prompt pack stays prompt TEXT: no links, no tool names that imply an endorsement, no personal
  data (the `links` and `pii` checks refuse them anyway).

## G3 — Assets with licence evidence (M)

| Asset key | Kind | Licence evidence needed | Used by |
|---|---|---|---|
| `font-inter` | font | OFL-1.1 (already the kit's font; re-verify if G1 changes fonts) | every item |
| `image-terakon-cover-placeholder` | image | owned or stock licence — **rename the key** to the real logo's key in `ebook.yaml` when G1 lands | playbook cover |

Register and verify each as a usable `factory.asset` on the instance before importing any source;
a font or image that is not verified, commercial and unexpired refuses the import by name.

## G4 — Renderers in the image (M)

- Workbooks: `openpyxl` (present; F3 proved it).
- Playbook: **Typst** for the PDF and **Pandoc** for the EPUB. Either missing from the image refuses
  with its own named error; a single-format release needs an explicit recorded choice on the work
  order (F4 runbook, `ops/runbooks/ebook-factory-rollout.md`).
- Course: the `html` renderer needs nothing.

## G5 — Pricing (M)

The prices in `product_lines/terakon.yaml` are placeholders. Set real prices (integer IDR) in the line
file, commit, and re-run the harness locally before touching the instance.

## G6 — Seller of record and live payments (M)

- **R2 seller of record**: R2 is still open on the program roadmap. Decide the entity (Odoo
  company) that sells Terakona digital products and its tax treatment, per
  `ops/runbooks/digital-delivery-rollout.md` M0 ("Decide the instance and seller of record"). R6
  refuses a cross-company checkout, so every Terakona product must live in that one company.
- **Live PAY1**: switch the Midtrans provider from the local fake to production keys per
  `ops/runbooks/midtrans-rollout.md`. The harness only ever uses the PAY1 fake and signer.

## G7 — Release and publish on the instance (M)

For each item, through public actions only (never a direct write): import → render per variant →
submit → checks → **expert approve → founder approve** → release → publish on the item's path
(`factory_digital` binding for the three workbooks; `factory_ebook_digital` for the playbook; the
`digital` course publisher for the course). One sellable product per item; a republish is idempotent.

## G8 — Launch (M)

Create the three F8 briefs named in the line (`terakon-prompt-pack-launch`,
`terakon-playbook-launch`, `terakon-course-launch`), pass their gate, approve, then schedule each
through F10 as `creator_handoff`. An F10 publication exists only after approval.

## Membership (not this runbook)

The "monthly drop" membership (TB-D5 default) is slice **MEM** (`factory_membership`, OCA `contract`):
each period's sale order carries that month's **released** drop products, delivered by R6 unchanged.
Its rollout is MEM's runbook. The B6 roadmap row closes when this line and MEM are both built-local.
