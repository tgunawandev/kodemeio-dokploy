# Runbook — timed restore drill (Odoo + Authentik, from the B2 offsite copy)

Roadmap row **0.6** (Wave 0 spec D11–D13, founder gate **G9**). A drill restores
from **B2** — the path that must work when Hetzner is down — into a throwaway,
network-isolated compose stack, validates at application level, and records
per-step timings, **RPO** (drill start − backup timestamp) and **RTO** (sum of
step times). Scripts: `ops/drills/odoo/drill-odoo.sh`,
`ops/drills/authentik/drill-authentik.sh`. Local tests:
`bash ops/drills/tests/test_drill_odoo.sh`, `bash ops/drills/tests/test_drill_authentik.sh`.

## Prerequisites

- **A Docker host that is NOT production**: Server B, or a temporary server the
  founder creates for the drill and deletes afterwards (G9 — Hetzner create and
  delete are the founder's hands only). ≥ 4 vCPU / 8 GB / disk ≥ 3× the largest
  dump + filestore. Run from your laptop with `--docker-host ssh://root@<host>`,
  or on the host itself with a checkout of this repo.
- `openssl`, `python3`, Docker Compose v2 on the machine running the script.
- **Free disk for the decompressed dump.** Each drill decompresses the fetched
  `.sql.gz` into its own temporary directory (`mktemp -d`, i.e. `$TMPDIR`) and
  reads it twice from there, so the host needs room for one whole
  uncompressed dump on top of the images and volumes — point `TMPDIR` at the
  big disk if `/tmp` is a small tmpfs.
- The drill stacks use `internal: true` networks: no mail, no webhooks, no
  Authentik/SSO calls leave the host; Odoo runs with crons off and mail servers
  deactivated; the Authentik worker has no docker.sock; nothing publishes a port.

## Environment (1Password → current shell only)

```bash
# B2 READ-ONLY key (never the mirror or master key)
export RCLONE_CONFIG_SRC_TYPE=s3 RCLONE_CONFIG_SRC_PROVIDER=Other RCLONE_CONFIG_SRC_ENV_AUTH=false
export RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$(op read 'op://Kodemeio/b2: kod-prod-backup-readonly/keyID')"
export RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$(op read 'op://Kodemeio/b2: kod-prod-backup-readonly/applicationKey')"
export RCLONE_CONFIG_SRC_ENDPOINT=https://s3.<region>.backblazeb2.com   # bucket's S3 endpoint
export AWS_ACCESS_KEY_ID="$RCLONE_CONFIG_SRC_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY"
# restic password of the filestore being drilled
export RESTIC_PASSWORD="$(op read 'op://Kodemeio/restic: kod-odoo-erp-filestore-backup/password')"
# Odoo image tag = what production runs (IMAGE_TAG=sha-… on the compose)
ODOO_IMAGE=ghcr.io/tgunawandev/kodemeio-odoo:sha-<prod-sha>
```

`AUTHENTIK_SECRET_KEY` is **not** needed: Authentik uses it only for cookie
signing, and the drill deliberately proves a restore works with a fresh key
(ops/wave0/facts.md).

## Run

```bash
cd kodemeio-dokploy
D=$(date -u +%F)

# Odoo erp (DB + filestore pair). Repeat for kod_odoo_hrms / kod_odoo_desk
# (see "which databases" below: they need --no-orders).
ops/drills/odoo/drill-odoo.sh --db-name kod_odoo_erp \
  --dump src:kod-prod-backup/kodemeio-postgres-backup/kod_odoo_erp/latest \
  --restic-repo "s3:${RCLONE_CONFIG_SRC_ENDPOINT}/kod-prod-backup/kodemeio-odoo-filestore/kod-odoo-erp" \
  --snapshot latest --odoo-image "$ODOO_IMAGE" \
  --docker-host ssh://root@<drill-host> \
  --out "ops/drills/results/${D}-kod_odoo_erp.json"

# Authentik (DB from kod postgres dumps + media tar)
ops/drills/authentik/drill-authentik.sh \
  --dump  src:kod-prod-backup/kodemeio-postgres-backup/authentik/latest \
  --media src:kod-prod-backup/kodemeio-authentik-backup/media/latest \
  --docker-host ssh://root@<drill-host> \
  --out "ops/drills/results/${D}-authentik.json"
```

### Which databases can be drilled, and with which override

The validate step cross-checks ONE model's row count against the SQL count taken
at restore time (`--orders-model`, default `sale.order`) — the check that proves
the restored database is the one the dump describes, not an empty skeleton:

| Database | Sales module? | How to call it |
|---|---|---|
| `kod_odoo_erp` | yes | default (`--orders-model sale.order`); nothing to pass |
| `kod_odoo_hrms` | no | `--no-orders`, or `--orders-model res.partner` to keep a cross-check |
| `kod_odoo_desk` | no | same as hrms |

- `--orders-model` takes a plain dotted Odoo model name (`res.partner` and
  `sale.order` are the two this runbook names; `res.partner` is in `base`, so it
  exists in every database). The drill derives the table from it, checks the
  table exists, and:
  - **table absent** ⇒ the cross-check is **skipped**, loudly, and the JSON
    records `validation.orders.skipped` with the reason. Not a failure — and not
    a pass either; the other checks (health, attachments, filestore, egress)
    still have to pass.
  - **table present, count unreadable** ⇒ the run **fails**
    (`failed_step: restore_db`). That is a real finding.
- `--no-orders` turns the cross-check off explicitly when no suitable model is
  known. Prefer `--orders-model res.partner`: a skipped check is a check you no
  longer have.
- The results JSON carries `validation.orders.model`, `.expected`, `.actual`
  when it ran, and `.skipped` when it did not — record that in the results
  table's status cell rather than leaving it ambiguous.


Exit 0 = `status: ok`. On failure the JSON still holds `status: failed`,
`failed_step` and every timing up to it; `--keep` leaves the stack up for
inspection (it prints the `down -v` command). If the production login flow
differs from `default-authentication-flow` (e.g. enforced MFA), pass
`--flow <slug>`; the validator names the stage it could not pass.

## Record the result

1. Add one row per run to `ops/drills/results/README.md` from the JSON — never
   hand-typed numbers, never a local test run. That file's column list and its
   "taken straight from a run's results.json" note are written for an **Odoo**
   run; an Authentik run's steps are named differently, so map them explicitly
   rather than leaving a cell half-meaning:

   | README column | Odoo (`drill-odoo.sh`) | Authentik (`drill-authentik.sh`) |
   |---|---|---|
   | backup age (RPO) | `rpo_seconds` | `rpo_seconds` |
   | fetch | `fetch_dump` | `fetch_dump` + `fetch_media` |
   | restore db | `restore_db` | `restore_db` |
   | filestore | `restore_filestore` | `restore_media` |
   | boot | `boot_odoo` | `boot` |
   | validate | `validate` | `validate` |
   | RTO total | `rto_seconds` | `rto_seconds` |

   Each drill has one step with no column — `neutralise` (Odoo, the SQL that
   deactivates mail servers and crons before boot) and `set_drill_password`
   (Authentik) — so name it in the status cell instead: `ok`, or
   `failed at neutralise`. `target` is `target_db` for Odoo and `target` for
   Authentik. If a run's own step names still do not fit the table, the JSON is
   the authority — widen the table in that commit rather than forcing a number
   into the wrong column.
2. `git add ops/drills/results/<file>.json ops/drills/results/README.md` and
   commit (`docs(ops): restore drill <target> <date>`).
3. Update the roadmap row **0.6** in
   `kodemeio-docs/superpowers/specs/2026-09-25-teracorp-program-roadmap.md`
   ("Today" column) with the measured RPO/RTO and a link to the committed JSON.
   The row moves to operational only with this evidence.

## Teardown

The scripts remove their own containers, networks and volumes (unless
`--keep`). Then: `docker system df` on the drill host to confirm nothing is
left, and the founder deletes the temporary server (G9). The drill leaves
production data on that disk until it is destroyed — do not reuse it.
