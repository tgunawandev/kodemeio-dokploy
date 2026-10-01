# Runbook — B2 offsite backup (restore when Hetzner is down)

**Scope:** the second copy of every idtpp (tpp + mac) backup, held at
Backblaze B2 so a Hetzner-wide outage, account lock or billing suspension does
not take the backups down together with production.

Every primary backup lands in Hetzner Object Storage (fsn1) — the same
provider that runs the servers being backed up. B2 (`us-west-004`) is a
different company on a different continent.

- **Code:** `kodemeio-skills` — `docker/lib/b2.sh`, `docker/jobs/b2-*.sh`,
  tests in `tests/test_b2_mirror.sh`
- **Desired state:** `deploys/instances/production/tpp-infra-kctl.yaml`
  (the four `b2-*` schedules) in this repo
- **Primary backups this copies:** [postgres-restore.md](postgres-restore.md),
  `kodemeio-odoo/docs/admin/filestore-backup.md`

## What is copied where

| B2 bucket / prefix | Hetzner source | Contents |
|---|---|---|
| `tpp-prod-backup/hz-tpp-postgres-backup/compose-input-multi-byte-port-qsokry_postgres/` | same path | pg_dumps of **tpp-infra-postgres**: tpp erp/hrms/helpdesk, tpp25, authentik, outline, nextcloud, zulip, glitchtip, prefect |
| `tpp-prod-backup/hz-tpp-odoo-filestore/` | `hz-tpp-odoo-filestore` | restic filestore repos (tpp-odoo-erp, tpp25-odoo-erp, xyops, and any new repo) |
| `tpp-prod-backup/hz-tpp-mattermost-backup/` · `…/hz-tpp-mattermost-data/` | same | Mattermost DB dumps + files |
| `tpp-prod-backup/tpp-outline/` · `…/tpp-nextcloud/` | same | Outline uploads, Nextcloud object store |
| `mac-prod-backup/hz-tpp-postgres-backup/compose-transmit-mobile-alarm-f4muk4_postgres/` | same path | pg_dumps of **mac-infra-postgres**: mac erp/hrms |
| `mac-prod-backup/hz-mac-odoo-filestore/` | `hz-mac-odoo-filestore` | restic repos mac-odoo-erp, mac-odoo-hrms |
| `mac-prod-backup/hz-mac-mattermost-backup/` · `…/hz-mac-mattermost-data/` | same | mac Mattermost |

mac has its own bucket and key, mirroring the Hetzner-side separation: a leaked
tpp credential cannot reach another legal entity's data.

**Deliberately NOT copied:** `hz-tpp-odoo-filestore/compose-*/` — Dokploy's
weekly volume tars, ~160 GB, a second copy of what restic already holds — and
`*-stg/`, the disposable staging canary. `tpp-glitchtip` is empty.

## Credentials — all in 1Password vault `Kodemeio`

| Item | Where it is used |
|---|---|
| `b2: tpp-prod-backup (MASTER key)` | **Laptop only, never a server.** Account-wide: creates buckets and keys, can delete anything. |
| `b2: tpp-prod-backup-mirror` | toolbox env `B2_TPP_KEY_ID` / `B2_TPP_APP_KEY`. Scoped to `tpp-prod-backup`. |
| `b2: mac-prod-backup-mirror` | toolbox env `B2_MAC_KEY_ID` / `B2_MAC_APP_KEY`. Scoped to `mac-prod-backup`. |
| `restic: <instance>-filestore-backup` | restic repository passwords — **without them the filestore copy is unreadable ciphertext.** |

The mirror keys have **no `deleteFiles`**. `rclone sync` therefore *hides* a
file that disappeared at the source; both buckets' lifecycle keeps hidden
versions **30 days**, then purges them. A compromised host can hide the offsite
copy, not destroy it. Verified 2026-09-25: a hard delete with a mirror key
returns `401 unauthorized`.

Both buckets: private, SSE-B2 (AES256) default encryption, unfinished large
uploads cancelled after 7 days.

## How it runs, and how you hear about it

Four jobs in the kctl toolbox (`tpp-infra-kctl`, tpp-prod-04), each wrapped by
`jobrun`, which **emails `ALERT_TO` on any failure**:

| Job | When (WIB) | Fails when |
|---|---|---|
| `b2-mirror-tpp` | :45 on even hours, skipping 06–08 | a source cannot be listed, is empty, shrank below half of B2, or rclone errors |
| `b2-mirror-mac` | :45 on odd hours, skipping 07 | same |
| `b2-fresh-tpp` | 05:40 daily | any watched prefix's newest B2 object is older than its threshold |
| `b2-fresh-mac` | 05:45 daily | same |

The fresh jobs are the real alarm. A mirror that stops being scheduled, or runs
"successfully" but never catches up, produces no failure of its own; only a
check of the B2 side sees it. They check **per database and per restic
repository** (30h for dumps and Mattermost, 8h for restic snapshots), so one
fresh prefix cannot hide a stale neighbour. B2 reports `LastModified` as the
*upload* time, so fresh means "the mirror copied something new recently".

A mirror run caps itself at **600s** (`B2_MIRROR_BUDGET`) and resumes on the
next run — rclone's "max duration reached" is recorded as `status=partial`, not
a failure. That is what lets the ~136 GB seed complete without jobrun's 900s
kill. The cap sits 300s below the kill because `--cutoff-mode soft` lets
in-flight transfers finish (measured: a 120s budget ran 212s).

Check by hand:

```bash
./dokploy.sh idtpp compose schedules list j_xoJf3TwylYAUmgPVLDn      # b2-* rows and last status
./dokploy.sh idtpp compose schedules run <schedule-id> --yes          # run one now
./dokploy.sh idtpp compose service-logs j_xoJf3TwylYAUmgPVLDn         # RESULT / SUMMARY lines
```

## Restore — Hetzner is down

Everything below runs from any machine with Docker and 1Password access. None
of it needs Hetzner, Dokploy or the kodemeio config.

Set up once per shell (use the **mirror** key of the tenant, read-only use):

```bash
export B2_ACCOUNT=<keyID from 1Password>  B2_KEY=<applicationKey>
R() { docker run --rm -e RCLONE_CONFIG_B2_TYPE=b2 -e RCLONE_CONFIG_B2_ACCOUNT="$B2_ACCOUNT" \
        -e RCLONE_CONFIG_B2_KEY="$B2_KEY" -v "$PWD:/w" rclone/rclone:1.75.1 "$@"; }
```

### A database

Dumps are `pg_dump -Fc` (custom format), gzipped, named `<UTC timestamp>.sql.gz`.

```bash
P=hz-tpp-postgres-backup/compose-transmit-mobile-alarm-f4muk4_postgres/mac-infra-postgres/mac_odoo_erp
R lsl "b2:mac-prod-backup/$P/" | sort -k2,3 | tail -3                  # newest last
R copyto "b2:mac-prod-backup/$P/<file>.sql.gz" /w/mac_odoo_erp.sql.gz
gunzip -c mac_odoo_erp.sql.gz | pg_restore -d <target-db> --no-owner --no-acl
```

Restore into the `kodemeio-postgres` image. A vanilla `postgres:16` reports two
ignored errors for the `pgaudit` extension it lacks; the data is unaffected.

Once a replacement Dokploy exists, B2 can instead be registered as a Dokploy S3
destination (provider `Other`, endpoint `https://s3.us-west-004.backblazeb2.com`)
and restored natively with `backups restore --destination <id> --file <key>`
([postgres-restore.md](postgres-restore.md) §2) — note the key carries the
extra `hz-tpp-postgres-backup/` prefix the mirror adds.

### An Odoo filestore (restic)

```bash
docker run --rm -e AWS_ACCESS_KEY_ID="$B2_ACCOUNT" -e AWS_SECRET_ACCESS_KEY="$B2_KEY" \
  -e RESTIC_PASSWORD='<1Password: restic: mac-odoo-erp-filestore-backup>' \
  -v "$PWD/restore:/restore" restic/restic:0.18.0 \
  -r s3:https://s3.us-west-004.backblazeb2.com/mac-prod-backup/hz-mac-odoo-filestore/mac-odoo-erp \
  restore latest --target /restore
```

Pair it with a database dump **no newer** than the snapshot — the same pairing
rule as the primary backups.

### Anything else (Mattermost, Outline, Nextcloud)

These are plain copies of the Hetzner buckets. Copy the prefix into whatever
S3 the replacement service points at:

```bash
R copy b2:tpp-prod-backup/tpp-outline <new-s3-remote>:<bucket> --fast-list
```

### A file that was deleted or overwritten

Hidden and superseded versions stay 30 days:

```bash
R lsf "b2:tpp-prod-backup/<prefix>/" --b2-versions      # name-v2026-09-24-230604-655.ext = old version
```

## Drill results

| Date | What | Result |
|---|---|---|
| 2026-09-25 | mac_odoo_erp dump pulled from B2 → `pg_restore` into Postgres 16 | gzip OK, `PGDMP` custom format, 1,403 tables; 1 company, 1,120 partners, 7,602 moves, 2,642 attachments; only errors: `pgaudit` absent |

## Gotchas

- **A planned purge at a source needs one run with `B2_MIRROR_ALLOW_SHRINK=1`**
  — the guard refuses any source holding under half of what B2 holds. Never set
  it persistently: that disables the alarm for good.
- **The mirror copies whatever the primary produced — a stale primary gives a
  stale copy.** On 2026-09-25 the tpp25 restic repo's newest snapshot was
  **17 days old** at the source, and the `tpp-odoo-hrms` / `tpp-odoo-helpdesk`
  filestore repos, declared 2026-09-07, **did not exist at all**. Fix those at
  the source; the mirror picks them up with no change here.
- The master key's secret was once pasted into a chat session. If that is a
  concern, rotate it in the B2 console and update the 1Password item — the
  mirror keys are separate and unaffected.
- `kctl-hz s3 freshness` prints a raw traceback (still exit 1, still alerts)
  when a key lacks access to the bucket, instead of a clean error.
