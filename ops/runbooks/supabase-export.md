# Supabase export — founder-run offsite copy (TeraKidz)

Wave 0 spec D17 / founder gate **G10**. Supabase is the provider-backed primary;
this runbook puts an **independent** copy of the database (roles, schema, data)
and every Storage object into Backblaze B2 (`kod-prod-backup/supabase/<name>/`),
outside Supabase and outside Hetzner. It is **not scheduled**: it needs the
database password, which only the founder holds.

| Item | Value |
|---|---|
| Project | TeraKidz production = **Supabase Cloud**, ref `etxfaitkiscjusjfzpuo` (`kodemeio-supabase/supabase.yaml`, target `terakidz/cloud`). No self-hosted production exists (`supa.terakidz.com` answers 404). |
| Script | `ops/scripts/supabase-export.sh` (dry-run by default, `--execute` to run) |
| CLI | Supabase CLI **2.116.0** (the version pinned in `kodemeio-supabase/.supabase-cli-version`); rclone ≥ 1.65 |
| Destination | `b2:kod-prod-backup/supabase/terakidz/<UTCSTAMP>/` = inventory item `supabase-terakidz` (`ops/backup-inventory.kod.yaml`) |
| Test | `bash ops/scripts/tests/test_supabase_export.sh` |

## When

- **Monthly** while TeraKidz is pre-launch (no real users/data) — inventory
  `fresh_h: 800` (~33 days).
- **Weekly** from the day real user data exists. In the same commit, change the
  inventory item's `fresh_h` to `192` (8 days) so the gap is visible.
- Before any destructive migration, plan change or project transfer.

## What the provider backup tier gives (H5 — founder reads the dashboard)

Open Dashboard → Project → Database → Backups and record here: plan, daily
backup retention, whether PITR is enabled, and whether Storage objects are
included (Supabase daily backups cover the database, **not** Storage objects).
Until this row is filled, assume: *database only, retention unknown, no PITR*.

| Date read | Plan | Daily backups kept | PITR | Storage covered |
|---|---|---|---|---|
| _(founder)_ | | | | |

## Credentials (1Password, never in git or shell history)

| Env var | 1Password item |
|---|---|
| `SUPABASE_DB_URL` | `supabase: terakidz-cloud db` — the **session pooler / direct** connection string from Dashboard → Connect, password percent-encoded |
| `RCLONE_CONFIG_SB_ACCESS_KEY_ID`, `RCLONE_CONFIG_SB_SECRET_ACCESS_KEY`, `RCLONE_CONFIG_SB_REGION` | `supabase: terakidz-cloud storage S3 key` (Dashboard → Storage → S3 → New access key; bypasses RLS — revoke after the run) |
| `RCLONE_CONFIG_B2_TYPE=b2`, `RCLONE_CONFIG_B2_ACCOUNT`, `RCLONE_CONFIG_B2_KEY` | `b2: kod-prod-backup-mirror` (key **without** `deleteFiles`, G2) |

Load them with `op` into the current shell only (e.g. `export SUPABASE_DB_URL="$(op read 'op://Kodemeio/supabase: terakidz-cloud db/url')"`),
never into a file. Run on your own single-user laptop: the Supabase CLI accepts
the connection string only as `--db-url`, so it is visible in the local process
table while a dump runs. The script's own output never shows it — every line is
filtered and the URL and password become `****` (the test proves this even when
the CLI echoes its arguments in an error).

## Run

```bash
cd kodemeio-dokploy
# 1. Dry run — prints the plan, the masked URL and the destination; calls nothing.
ops/scripts/supabase-export.sh --project-ref etxfaitkiscjusjfzpuo --name terakidz

# 2. Execute.
ops/scripts/supabase-export.sh --project-ref etxfaitkiscjusjfzpuo --name terakidz --execute
```

Expected tail: `RESULT=ok out=b2:kod-prod-backup/supabase/terakidz/<stamp>/ work=<dir>`.
Any failing step prints `FAILED <step>` and uploads **nothing**.

Verify, then clean up:

```bash
rclone lsl b2:kod-prod-backup/supabase/terakidz/<stamp>/ | head
rclone cat b2:kod-prod-backup/supabase/terakidz/<stamp>/manifest.json | head -30
rm -rf <work dir>          # the local copy contains production data
```

Revoke the Storage S3 key in the dashboard after the run.

## Restore into a sandbox project (drill)

Never restore into production. Create a throwaway project (Supabase Cloud free
project or `./supabase.sh terakidz local ...`), then:

```bash
rclone copy b2:kod-prod-backup/supabase/terakidz/<stamp>/ ./restore/
cd restore && python3 - <<'PY'   # integrity: every file matches manifest.json
import hashlib, json
m = json.load(open("manifest.json"))
bad = [f["path"] for f in m["files"]
       if hashlib.sha256(open(f["path"], "rb").read()).hexdigest() != f["sha256"]]
print("BAD", bad) if bad else print("manifest OK", len(m["files"]))
PY

# DB (sandbox connection string in SANDBOX_DB_URL):
psql "$SANDBOX_DB_URL" -v ON_ERROR_STOP=1 -f roles.sql      # role errors for built-in roles are expected; review them
psql "$SANDBOX_DB_URL" -v ON_ERROR_STOP=1 -f schema.sql
psql "$SANDBOX_DB_URL" -v ON_ERROR_STOP=1 -c 'SET session_replication_role = replica' -f data.sql

# Storage: recreate the buckets in the sandbox, then
rclone copy ./storage/ sandbox-sb:     # sandbox S3 remote, same RCLONE_CONFIG_* pattern
```

Check: row counts of the main tables equal the production counts taken at export
time; a sample of Storage objects downloads through the sandbox API; auth users
can sign in (their password hashes are in `auth.users`).

## Record

Append a row (date, stamp, file count, total bytes, restore-drilled yes/no) to
the table below and commit.

| Date | Stamp | Files | Bytes | Sandbox restore |
|---|---|---|---|---|
| _(first run)_ | | | | |
