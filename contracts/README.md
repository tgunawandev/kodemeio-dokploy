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
- Tests: `uv run pytest deploys/tests -k contracts`.
