# Restore drill results

A row is only added here from a **real drill's** `results.json` — never
hand-written, never from a local test run (`tests/test_drill_odoo.sh` writes
its own results to a temp directory and never touches this file). A drill
run's `results.json` is committed alongside its row as evidence.

| date | target | backup age (RPO) | fetch | restore db | filestore | boot | validate | RTO total | status | results file |
|---|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — | — |

Columns, taken straight from a run's `results.json` (see
`drill-odoo.sh`/`validate_odoo.py` for the exact schema):

- **date** — `started_at`
- **target** — `target_db`
- **backup age (RPO)** — `rpo_seconds`, formatted `Xh Ym`
- **fetch / restore db / filestore / boot / validate** — each step's
  `seconds` from the `steps` array
- **RTO total** — `rto_seconds`
- **status** — `status` (`ok` / `failed`, plus `failed_step` if failed)
- **results file** — path to the committed `results.json` for that run,
  e.g. `results/2026-10-01-kod_odoo_erp.json`

The placeholder row above is removed the first time a real drill's
`results.json` is committed here.
