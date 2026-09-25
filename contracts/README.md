# Contracts

Versioned, machine-checked contracts shared by Teracorp services (kodeme.io estate).
Owners: this repo holds the schemas; each service repo implements them.

- One file per major version: `<name>.v<major>.schema.json`. Adding optional fields is a
  minor change in place; removing fields or making fields required needs a new major file.
- `$id` = `https://kodeme.io/contracts/<path>`; schemas reference each other by `$id`.
- Envelopes carry references, never personal data: `financial` and `child` data classes are
  not allowed on any v1 event.
- Tests: `uv run pytest deploys/tests -k contracts`.
