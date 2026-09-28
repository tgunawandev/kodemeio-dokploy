# Contracts

Versioned, machine-checked contracts shared by the kod (kodeme.io) estate's services.
Owners: this repo holds the schemas; each service repo implements them.

- One file per major version: `<name>.v<major>.schema.json`. Adding optional fields is a
  minor change in place; removing fields or making fields required needs a new major file.
- `$id` = `https://kodeme.io/contracts/<path>`; schemas reference each other by `$id`.
- Envelopes carry references, never personal data: `financial` and `child` data classes are
  not allowed on any v1 event.
- `classification.yaml` gates two distinct LLM routes: `allowed_to_third_party_llm` (any
  external provider) and the narrower `allowed_to_vetted_llm` (a specific, founder-approved
  LiteLLM group, e.g. `grp-personal` — no training, bounded retention). Only `personal` data
  may take the vetted route today (`# FOUNDER DECISION PENDING (2026-09-26)`); `financial` and
  `child` are never allowed to any LLM route. An agent profile declares which route it uses via
  `data_scope.llm` (`agents/profile.v1.schema.json` also rejects `max_classification: personal`
  paired with `llm: third_party` structurally, via `if`/`then`). **`data_scope.llm` absent means
  the profile may send NO personal data to any LLM** — vetted or third-party; it is not an
  implicit grant (e.g. `agents/kido.yaml`, which predates this key). If the founder declines the
  pending approval, `classification.yaml`'s `personal.allowed_to_vetted_llm` goes back to
  `false` and `kido_chat` runs `KIDO_ENABLED=0` (handoff-only, spec D12) until an alternative
  route is approved (spec §8 M4). `kido_chat`'s runtime must itself enforce `llm: vetted` — it
  is issued only the `kido` LiteLLM key (scoped to `grp-personal`), never a third-party key.
- `agents/profile.v1.schema.json`'s `profile` field pattern was widened from
  `^[a-z][a-z0-9-]{1,31}$` to `^[a-z][a-z0-9_-]{1,31}$` (allows `_`) so `kido_chat` validates;
  by convention a profile's `profile:` value equals its file's stem.
- `work_orders/work_order.v1.schema.json`'s `kind` gains `factory_job` (F0 factory
  commons, additive-only widening — the compat test pins this). A factory job may also carry
  `factory` (the production line: `website`, `template`, `ebook`, `course`, `video`, `social`,
  `affiliate`, `software`) and `gate` (the stage reached: `auto`, `expert`, `founder`). `refs`
  gains optional `odoo_factory_work_order` and `odoo_landing_page_version`, pointing at the
  Odoo-side ledger record (`factory.work.order`) and the landing page version it materialises.
  The pre-existing `order` shape is untouched.
- `events/order.requested.v1.schema.json`'s `payload.channel` carries an annotation-only
  `description` (no validation change): for `marketplace`, the intake event id MUST be the
  stable `<provider>:<order_id>`, never a delivery, poll or attempt id, and identity is
  company-scoped (`idempotency_key = marketplace:<company>:<event_id>`; other channels
  `<source>:<event_id>`), so the same order arriving via several sources lands once. `kodemeio-hatchet` `order_intake` enforces the shape at intake
  and vendors a byte-identical copy of this schema.
- `brands/brand_kit.v1.schema.json` is a brand's or niche channel's voice, audience, do/don't
  rules, forbidden phrases, required disclaimers (incl. AI and affiliate disclosure), a subset
  of the renderer's 13 colour/radius tokens, fonts (`asset:<key>` references — every referenced
  font must also appear in `assets`), a WhatsApp contact number, and expert reviewer logins.
  `brands/<code>.yaml` in this repo is the git source of truth (PR-reviewed); each consuming
  service imports its own read-only snapshot (e.g. Odoo's `factory_base`, per the F0
  factory-commons plan) rather than editing the kit directly. **The brand registry is data:** a brand code is
  valid iff `brands/<code>.yaml` exists. Every `brand` field (work_order.v1, brand_kit.v1, agent
  profile.v1, the ops hook-library contracts) pins only the slug `^[a-z][a-z0-9-]{1,31}$`, and
  `deploys/tests/test_brand_registry.py` cross-checks every committed reference, so a new brand
  is one kit file, never a schema or code edit. The schemas alone no longer close these
  values: a consumer that validates only against a schema must also consult the registry
  (`brands/`, `entitlements/apps.v1.json`) or it will accept an unregistered code. `brands/terakidz.yaml` is
  `active`; `brands/terakon.yaml` is a `draft` test fixture only (no real product yet).
- `templates/template.v1.schema.json` is a factory template (template factory, F3):
  a committed `templates/<id>/template.yaml` plus the layout sidecar it names (`.html` for
  `typst`/`wkhtml`, `.yaml`/`.json` for `xlsx` — the renderer pins the extension). **A layout is
  never a program** (spec D2 amendment, 2026-09-27): the HTML sidecar is a skeleton the renderer
  FILLS ({{blocks}} and the kit's tokens), and the workbook sidecar is DATA the renderer
  INTERPRETS — `sheets → rows → cells`, each cell one of `text`/`var`/`block`/`formula`, with a
  closed style set (`title`, `note`, `header`, `body`, `grid`, `footer`, `notice`) and a closed
  formula set (`SUM`, `AVG`, `MIN`, `MAX`, `COUNT` over a cell or a range). A `.py` sidecar is
  refused by the contract, so a merged template can never become code inside a renderer. It pins
  the artefact `kind` (`pdf`|`xlsx`), the render `renderer`, the page box (size, orientation,
  margins in mm), a **closed block vocabulary** (`heading`, `paragraph`, `list`, `table`,
  `image`, `spacer`, `footer`, `disclaimer` — never free-form HTML), typed `variables`
  (`string`|`int`|`date`|`enum`, with `required`/`default`/`enum`), licensed `assets`
  (`fonts` and `images` role maps — every value an `asset:<key>` reference, so a raw URL is
  never a template value: the licence lives with the asset), `outputs` and the `brand.kits` a
  template is approved to render under. Every string is literal text or a placeholder:
  `{{var.<key>}}`, `{{theme.<token>}}` (the kit's palette and font families), `{{page.<token>}}`,
  `{{option.<locale|palette|page_size|orientation>}}`, `{{kit.<disclaimers|ai_disclosure>}}`,
  and `{{blocks}}` in a layout source. The human label is `title`, not `name`: `name` is
  refused by the shared PII property-name denylist in `deploys/tests/contracts_lib.py`. The
  two shipped examples are `templates/terakidz-learning-pack/` (PDF) and
  `templates/terakon-planner/` (workbook); `deploys/tests/test_contracts_templates.py` checks
  both the schema and the semantics it cannot express (every placeholder resolves, every image
  block names a declared role).
- `ebooks/ebook.v1.schema.json` is an e-book (e-book factory, F4): a committed
  `ebooks/<id>/ebook.yaml` plus `book.md`, the book's **canonical Markdown source** — the ONE
  artefact both renderers consume (Typst → PDF, Pandoc → EPUB, `renderers.pdf`/`renderers.epub`
  naming each as data; a renderer the consumer lacks refuses by name, `typst not installed` /
  `pandoc not installed`, and there is no fallback). `book.md` is **derived** from the chapters,
  never authored, and the derivation is pinned byte for byte in both repos
  (`deploys/tests/test_contracts_ebooks.py::render_markdown` and the consumer's
  `factory_ebook._fe_markdown`), so the prose a human reads and the data the factory renders
  from cannot drift. It pins the book metadata (`title`/`subtitle`/`author`/`locale` — the human
  labels are not `name`, which the shared PII denylist refuses), the page box, a **typed cover
  block set** (the book's own metadata plus `cover.logo`, an `asset:<key>` reference, and
  `cover.palette`, a KIT colour ROLE — never a hex value, never free-form HTML), the chapter
  tree, typed `variables`, licensed `assets`, `outputs` (`pdf` required, `epub` optional — a
  single-format release must be an explicit recorded choice on the work order, spec A5) and the
  `brand.kits` a book is approved to render under. The chapter block vocabulary is F3's
  `template.v1` vocabulary **shared by reference as a pinned copy**:
  `test_the_block_vocabulary_is_f3s_pinned_copy` fails the moment the two contracts disagree.
  The one shipped book is `ebooks/terakidz-example/`.
- `courses/course.v1.schema.json` is a factory course (course factory, F5): a committed
  `courses/<id>/course.yaml` plus the Markdown sources it names in `sources` (a source KEY →
  file name map). **A source is Markdown, never a program** — the file names are pinned to
  `.md`, exactly as `template.v1` pins its layout sidecar, so a `.py`/`.html` source is refused
  by the contract before any consumer reads it. It pins the course metadata (`title`, `level`,
  `locale` — the human label is `title`, not `name`, which the shared PII denylist refuses),
  the SLIDES `renderer` (`html` always available, `typst` refuses by name when the package is
  absent — there is **no Marp and no Node toolchain** behind either: a source is Markdown, `---`
  separates slides, and the renderer builds every tag itself), the module → lesson tree (each
  lesson names its `reading` and `slides` source keys, an optional `video` `asset:<key>`
  reference — F6 owns production — and an optional `quiz`), a **closed quiz shape**
  (`pass_score`, questions with `options`, an `answer` that must be one of them and a required
  `explanation` — JSON Schema states the shape, the consumer's quiz check refuses an answer no
  option carries, by name), typed `variables`, licensed `assets` (`fonts` required, `images`
  and `footage` role maps, every value an `asset:<key>` reference so a raw URL is never a
  course value), `outputs` (`html` always, `pdf` when the renderer produces it) and the
  `brand.kits` a course is approved to render under. A source's prose is literal text or a
  placeholder: `{{var.<key>}}`, `{{theme.<token>}}`, `{{option.<locale|palette|page_size>}}`,
  `{{kit.<disclaimers|ai_disclosure>}}`, `{{course.<title|level|locale>}}` and
  `{{lesson.<title|id|module>}}`. The one shipped course is `courses/terakidz-komunikasi-dasar/`;
  `deploys/tests/test_contracts_courses.py` checks the schema AND the semantics it cannot
  express (every placeholder resolves, every lesson's source keys are declared and on disk, every
  quiz answer is one of its options, and every source stays inside the closed Markdown
  vocabulary the renderer implements).
- `product_lines/product_line.v1.schema.json` is a product line as data (Track B, K1): a committed `product_lines/<id>.yaml` naming its `brand_kit`, `items` (`kind` template|ebook|course|content_piece|physical, `ref` to a committed source id, optional per-item `kit`, `variants` setting only declared variables, `price_idr` ≥0 with 0 = free, a seeded `digital_kind`, `publish` factory_digital|digital|none) and `launch` F8 briefs (`platform`, `shape`, `brief_key`, optional F10 `publish_mode`); no value is ever a URL, and `deploys/tests/test_contracts_product_lines.py` resolves every ref and kit against this repo. Draft (placeholder) kits — `terakon`, `terafin`, `terakod` — must carry a `kit uji coba` required disclaimer (`deploys/tests/test_placeholder_kits.py`).
- `entitlements/app_checkout.v1` and `entitlements/entitlement.v1` define the app entitlement bridge (ENT, TB-D1). An app asks Odoo PAY1 for a checkout using opaque refs only: `app` (a slug, valid iff listed in the data registry `entitlements/apps.v1.json`), a uuid `app_account_ref`, `plan_code`, `idempotency_key` and `issued_at`. Odoo replies later with a signed `active` or `revoked` event carrying an `event_id` ULID, `valid_from` and `valid_until`. Both directions use HMAC-SHA256 with the `order_intake` scheme under generic `X-Webhook-*` header names. `entitlements/README.md` fixes the header names, the time window and the semantics. `examples/entitlements/signing.v1.vector.json` is the byte-exact test vector (`deploys/tests/test_contracts_entitlements.py`).
- Tests: `uv run pytest deploys/tests -k contracts`.
