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
  `data_scope.llm`.
- Tests: `uv run pytest deploys/tests -k contracts`.
