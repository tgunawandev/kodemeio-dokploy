# Runbook — kod offsite backup (restore when Hetzner is down)

kodeme.io estate only (Teracorp). Nothing outside this estate — no other
bucket, key, job or host — is touched, depended on, or reachable from these
credentials. Design: Wave 0 spec D1–D5
(`kodemeio-docs/superpowers/specs/2026-09-26-teracorp-wave0-ops-design.md`).

**Status:** built locally, **not deployed.** The toolbox (`kod-infra-kctl`) ships
with `sha-PENDING` and all schedules `enabled: false`; go-live is the ordered
founder procedure in [teracorp-wave0-rollout.md](teracorp-wave0-rollout.md).

## What is copied where

Source of truth: `ops/backup-inventory.kod.yaml` (a test fails if the jobs and
the inventory disagree). Offsite = Backblaze B2 bucket **`kod-prod-backup`**;
the mirror keeps the Hetzner bucket name as the first path segment.

The freshness column is the prefix the two `*-fresh` jobs actually watch
(`kodemeio-skills/docker/jobs/kod-{offsite,hz}-fresh.sh`, kept equal to the
inventory by `deploys/tests/test_backup_inventory.py`) — for a restic store
that is the repo's own `snapshots/` directory, not the repo root.

| Item | Primary (Hetzner S3, kodemeio project) | Offsite (B2 `kod-prod-backup`) | Fresh ≤ h |
|---|---|---|---|
| Odoo erp DB | `kodemeio-postgres-backup/kod_odoo_erp/` | `kodemeio-postgres-backup/kod_odoo_erp/` | 30 |
| Odoo erp filestore (restic repo `…/kod-odoo-erp`) | `kodemeio-odoo-filestore/kod-odoo-erp/snapshots/` | `kodemeio-odoo-filestore/kod-odoo-erp/snapshots/` | 8 |
| Odoo hrms DB | `kodemeio-postgres-backup/kod_odoo_hrms/` | `kodemeio-postgres-backup/kod_odoo_hrms/` | 30 |
| Odoo hrms filestore (restic repo `…/kod-odoo-hrms`) | `kodemeio-odoo-filestore/kod-odoo-hrms/snapshots/` | `kodemeio-odoo-filestore/kod-odoo-hrms/snapshots/` | 8 |
| Odoo desk DB | `kodemeio-postgres-backup/kod_odoo_desk/` | `kodemeio-postgres-backup/kod_odoo_desk/` | 30 |
| Odoo desk filestore (restic repo `…/kod-odoo-desk`) | `kodemeio-odoo-filestore/kod-odoo-desk/snapshots/` | `kodemeio-odoo-filestore/kod-odoo-desk/snapshots/` | 8 |
| Authentik DB (on kod postgres) | `kodemeio-postgres-backup/authentik/` | `kodemeio-postgres-backup/authentik/` | 30 |
| Authentik media (Task 6 sidecar) | `kodemeio-authentik-backup/media/` | `kodemeio-authentik-backup/media/` | 30 |
| Mattermost DB | `kodemeio-mattermost-backup/mattermost/daily/` | `kodemeio-mattermost-backup/mattermost/daily/` | 30 |
| Mattermost files (`mm-data` volume, Task 6 sidecar) | `kodemeio-mattermost-backup/mattermost/files/` | `kodemeio-mattermost-backup/mattermost/files/` | 30 |
| Hatchet DB | `kodemeio-hatchet-backup/db/` | `kodemeio-hatchet-backup/db/` | 30 |
| Supabase TeraKidz (Cloud) | provider backups | `supabase/terakidz/` (founder-run, [supabase-export.md](supabase-export.md)) | 800 |

The B2 key is the Hetzner bucket name with `kod-prod-backup/` in front, so any
path below is read as `b2:kod-prod-backup/<the Offsite column>`. Deliberately
NOT mirrored: Dokploy's weekly volume tars (`kodemeio-odoo-filestore/compose-*/`)
and any `*-stg/` canary — restic already holds a better copy of the first. Both
are `--exclude` flags on the filestore `b2_sync` in
kodemeio-skills' `docker/jobs/kod-offsite-mirror.sh`, and
`tests/test_kod_jobs.sh` asserts both reach rclone (final review M1: the
sentence was true for `compose-*/**` only until 2026-09-27, and the stg canary
had been copied).

Gaps (no backup — listed in the inventory with a reason): LiteLLM DB (not
deployed), Mattermost config volume, Dokploy control plane, Hermes state, dsh
home, Mailcow.

## Credentials — 1Password vault `Kodemeio`

| Item | Where it lives | Can |
|---|---|---|
| `b2: kod-prod-backup (MASTER key)` | founder laptop only, never a server | everything incl. delete — used only to create the two keys below, set the lifecycle, and clear the G2 hard-delete probe |
| `b2: kod-prod-backup-mirror` | toolbox env `B2_KOD_KEY_ID` / `B2_KOD_APP_KEY` | list/read/write, **no `deleteFiles`** (a hard delete returns 401) |
| `b2: kod-prod-backup-readonly` | toolbox env `B2_KOD_RO_KEY_ID` / `B2_KOD_RO_APP_KEY`; drills | list/read |
| `restic: kod-odoo-erp-filestore-backup`, `-hrms-`, `-desk-` | filestore-backup composes | restic repo passwords |
| `hatchet: encryption keysets` | Hatchet env `SERVER_ENCRYPTION_MASTER_KEYSET`, `…_JWT_PRIVATE_KEYSET`, `…_JWT_PUBLIC_KEYSET` | **without them a restored Hatchet DB is unreadable** |
| Healthchecks.io ping URLs | toolbox env `HC_KOD_OFFSITE_MIRROR`, `HC_KOD_OFFSITE_FRESH`, `HC_KOD_HZ_FRESH`; Gatus `HC_GATUS_URL` | whoever holds one can silence that alarm |

Bucket lifecycle: keep prior versions 30 days (`daysFromHidingToDeleting: 30`),
SSE-B2 on.

## How it runs, and how you hear about it

Toolbox `kod-infra-kctl` (compose `kodemeio-skills/compose/toolbox-kod.yml`,
host = founder choice, never the Teracorp production server). Dokploy schedules
run `jobrun <name>`:

| Job | Cron (UTC) | Does | Healthchecks check (period / grace) |
|---|---|---|---|
| `kod-offsite-mirror` | `45 */2 * * *` | `rclone sync` each source → B2, biggest-risk first; refuses empty sources and shrink below half; 600 s budget, resumes next run | `kod-offsite-mirror` 2 h / 1 h |
| `kod-hz-fresh` | `40 23 * * *` | newest object per primary prefix ≤ fresh h | `kod-hz-fresh` 1 d / 2 h |
| `kod-offsite-fresh` | `50 23 * * *` | same, on the B2 copy | `kod-offsite-fresh` 1 d / 2 h |

You hear about it three ways: `jobrun` emails `ALERT_TO` on any failure;
each job pings Healthchecks.io on success and `/fail` on failure (a partial
mirror run pings **nothing** — the grace period turns a stuck seed into an
alarm); and Healthchecks alerts Telegram + email when a ping is late. Every run
prints `RESULT=` / `SUMMARY` lines — read them in the schedule's log.

## Restore — Hetzner is down

Runs from any machine with Docker and 1Password; needs no Hetzner, Dokploy or
kctl config. Use the **read-only** key.

```bash
export B2_ACCOUNT="$(op read 'op://Kodemeio/b2: kod-prod-backup-readonly/keyID')"
export B2_KEY="$(op read 'op://Kodemeio/b2: kod-prod-backup-readonly/applicationKey')"
R() { docker run --rm -e RCLONE_CONFIG_B2_TYPE=b2 -e RCLONE_CONFIG_B2_ACCOUNT -e RCLONE_CONFIG_B2_KEY \
        -v "$PWD:/w" rclone/rclone:1.75.1 "$@"; }
export RCLONE_CONFIG_B2_ACCOUNT="$B2_ACCOUNT" RCLONE_CONFIG_B2_KEY="$B2_KEY"
B2_S3=https://s3.<region>.backblazeb2.com    # the bucket's S3 endpoint (B2 console → bucket details)
```

### An Odoo database (erp / hrms / desk)

Dumps are `pg_dump -Fc`, gzipped, named `<ISO timestamp>.sql.gz`.

```bash
R lsf b2:kod-prod-backup/kodemeio-postgres-backup/kod_odoo_erp/ | sort | tail -3     # newest last
R copyto "b2:kod-prod-backup/kodemeio-postgres-backup/kod_odoo_erp/<file>.sql.gz" /w/erp.sql.gz
createdb -h <target> -U <user> kod_odoo_erp
gunzip -c erp.sql.gz | pg_restore -h <target> -U <user> -d kod_odoo_erp --no-owner --no-acl
```

### An Odoo filestore (restic 0.19.1, against the B2 S3 endpoint)

```bash
docker run --rm -e AWS_ACCESS_KEY_ID="$B2_ACCOUNT" -e AWS_SECRET_ACCESS_KEY="$B2_KEY" \
  -e RESTIC_PASSWORD="$(op read 'op://Kodemeio/restic: kod-odoo-erp-filestore-backup/password')" \
  -v "$PWD/restore:/restore" restic/restic:0.19.1 \
  -r "s3:${B2_S3}/kod-prod-backup/kodemeio-odoo-filestore/kod-odoo-erp" \
  restore latest --target /restore
```

Pair with a dump **no newer than** the snapshot (pairing rule). Copy
`restore/…/filestore/<db>` into the new Odoo's `odoo-filestore` volume at
`/var/lib/odoo/filestore/kod_odoo_erp`. The drill
([restore-drill.md](restore-drill.md)) runs exactly this end to end.

### Authentik

DB = the `authentik` dump above (same `pg_restore` into the `authentik` DB);
media = `kodemeio-authentik-backup/media/authentik-media-<stamp>.tar.gz`,
extracted into the new `authentik-data` volume (`tar -xzf … -C /data`). A NEW
`AUTHENTIK_SECRET_KEY` works — it only signs cookies (everyone is logged out);
the drill proves it. If `AUTHENTIK_STORAGE_BACKEND=s3` in production, media is
in Authentik's own S3 bucket instead (rollout G6).

### Mattermost

DB: newest `kodemeio-mattermost-backup/mattermost/daily/*` → `pg_restore`/`psql`
per its format (kodemeio-mattermost `backup/backup.sh`). Files: newest
`…/mattermost/files/mattermost-files-*.tar.gz` → extract into the `mm-data`
volume (`/mattermost/data`). Config is a gap: rebuild from the compose env.

### Hatchet (keysets!)

Newest `kodemeio-hatchet-backup/db/hatchet-<stamp>.sql.gz` is plain SQL:
`gunzip -c … | psql -d hatchet`. Then start Hatchet with the **same**
`SERVER_ENCRYPTION_*` keysets from 1Password — with new keysets every stored
token and secret is unreadable and every worker must be re-registered.

### A file deleted or overwritten upstream

The mirror `rclone sync`s, so an upstream delete becomes a **hidden version**
on B2 (kept 30 days by lifecycle; the mirror key cannot hard-delete):

```bash
R lsf "b2:kod-prod-backup/<prefix>/" --b2-versions
```

## Drill results

Recorded in `ops/drills/results/README.md` (procedure: [restore-drill.md](restore-drill.md)).

## Gotchas

- **A planned purge at a source needs ONE run with `B2_MIRROR_ALLOW_SHRINK=1`**
  (the guard refuses a source holding under half of what B2 holds). Never set it
  persistently.
- **A stale primary gives a stale copy.** `kod-hz-fresh` exists for this: at
  audit time `kod_odoo_hrms` had one dump, 223 h old, and the erp/hrms restic
  repos were unconfirmed. Fix at the source.
- **Registration is not verification.** A schedule existing, a key existing or
  a green deploy proves nothing — watch a run and read its `RESULT` line, and
  read the Healthchecks check going green.
- The mirror key must never gain `deleteFiles`; re-run the hard-delete 401 test
  after any key change (rollout G2).
