# Contracts

Versioned, machine-checked contracts shared by Teracorp services (kodeme.io estate).
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
- `work_orders/work_order.v1.schema.json`'s `kind` gains `factory_job` (Teracorp factory
  commons, additive-only widening — the compat test pins this). A factory job may also carry
  `factory` (the production line: `website`, `template`, `ebook`, `course`, `video`, `social`,
  `affiliate`, `software`) and `gate` (the stage reached: `auto`, `expert`, `founder`). `refs`
  gains optional `odoo_factory_work_order` and `odoo_landing_page_version`, pointing at the
  Odoo-side ledger record (`factory.work.order`) and the landing page version it materialises.
  The pre-existing `order` shape is untouched.
- `brands/brand_kit.v1.schema.json` is a brand's or niche channel's voice, audience, do/don't
  rules, forbidden phrases, required disclaimers (incl. AI and affiliate disclosure), a subset
  of the renderer's 13 colour/radius tokens, fonts (`asset:<key>` references — every referenced
  font must also appear in `assets`), a WhatsApp contact number, and expert reviewer logins.
  `brands/<code>.yaml` in this repo is the git source of truth (PR-reviewed); each consuming
  service imports its own read-only snapshot (e.g. Odoo's `factory_base`, per the Teracorp
  factory-commons plan) rather than editing the kit directly. `brands/terakidz.yaml` is
  `active`; `brands/terakon.yaml` is a `draft` test fixture only (no real product yet).
- Tests: `uv run pytest deploys/tests -k contracts`.
