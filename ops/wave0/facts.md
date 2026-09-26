# Teracorp Wave 0 — verified facts (kodeme.io estate)

Facts established while building Wave 0, with evidence. Read-only findings;
nothing here was changed in production.

## Authentik restore (Task 8)

| Fact | Evidence |
|---|---|
| A restored Authentik DB does **not** need the original `AUTHENTIK_SECRET_KEY`: the key only signs cookies ("Changing this will invalidate active sessions"); since 2023.6 it no longer feeds unique user IDs. A restore with a new key = everyone logged out, nothing else. | goauthentik docs `install-config/configuration/configuration.mdx` (2026.2) via context7; **proven** by `ops/drills/tests/test_drill_authentik.sh`, which restores with a fresh random key and logs in |
| Backup scope per upstream docs: the PostgreSQL DB (essential) + `/data` (media, only if not on S3) + `/certs`, `/custom-templates`, `/blueprints` if used | `sys-mgmt/ops/backup-restore.mdx` |
| Live Authentik DB is the `authentik` DB on kod postgres (dumped to `kodemeio-postgres-backup/authentik/`, `pg_dump -Fc` piped through `gzip`); media tar = `kodemeio-authentik-backup/media/authentik-media-<UTCSTAMP>.tar.gz` | Task 0 H2; kodemeio-authentik `backup/backup.sh` |
| Flow executor on `ghcr.io/goauthentik/server:2026.2.3`: `GET/POST /api/v3/flows/executor/default-authentication-flow/?query=next%3D%2F`; challenges `ak-stage-identification` (`uid_field`) → `ak-stage-password` (`password`) → `xak-flow-redirect`; no CSRF header needed for this API with a session cookie | run against a local 2026.2.3 container, 2026-09-26 |
| A fresh 2026.2.3 has users `akadmin`, `AnonymousUser`, one `ak-outpost-…` service user, groups `authentik Admins`, `authentik Read-only`; `/data` holds only a `media -> /media` symlink | same |
| Restore gotcha: `ak shell -c` via `docker compose exec -T` blocks when stdin is left open — always `</dev/null` | observed while building the drill |
| **`pg_restore --list` must NOT be fed through a pipe.** The custom format puts its TOC early, so `--list` stops reading as soon as it has it; a piped producer then dies of SIGPIPE (exit 141), and under `set -o pipefail` that reports as a *failed* archive check on a perfectly good dump. Decompress to a file once and feed both `--list` and the restore by redirection. | measured 2026-09-26 on a 4.4 MB `pg_dump -Fc`: `gunzip -c x.gz \| docker exec -i … pg_restore --list` → `PIPESTATUS` `gunzip=141 pg_restore=0`, pipeline rc 141, full listing produced; same archive by redirect → rc 0 |
| **`--exit-on-error` is wrong for this restore.** A kod production dump is taken from the `kodemeio-postgres` image, which carries extensions and comments a vanilla `postgres:16-alpine` cannot apply; `--exit-on-error` turns the first of those into a dead restore. The drill validates the archive with `--list` first, restores tolerantly, then judges success on the user count read back from SQL — and records the pg_restore error count as `restore_errors` so nothing is hidden. | kod production postgres runs the `kodemeio-postgres` image (`kodemeio-postgres/scripts/s3.sh`); the Odoo drill's report records the same `pgaudit` mismatch; `drill-authentik.sh` restore_db |
| A media tar taken from `/data` carries a `media -> /media` **symlink** beside the real files, so "how many files came back" must count non-directory entries (`find ! -type d`), not `find -type f` — the latter silently drops the symlink and the count never reconciles. | `tar -tzf authentik-media-<stamp>.tar.gz` → 3 non-dir entries; `find -type f` after extracting → 2, `! -type d` → 3 |
| A cold `ghcr.io/goauthentik/server:2026.2.3` against an empty database spends ~4–5 min applying migrations before `/-/health/ready/` answers; the server sits at "waiting to acquire database lock" while the worker migrates. Budget for it in a drill timeout, not for it in a failure. | fixture boots on this workstation, 2026-09-26: 20:57:26 compose up → 21:00:30 ready |
| **`server` and `worker` cannot share a FRESH named volume under Compose.** Docker copies an image's content into a named volume on first mount only while that volume is empty, and Compose creates these two services concurrently (`create`/`up` have no `--parallel` here), so both try to populate it and one dies with `failed to create symlink: …/_data/media: file exists` — the image's `/data` carries a `media -> /media` symlink. Both drill scripts now create and populate the volume once, serially (`docker run --entrypoint /bin/sh -v <vol>:/data <image> -c true`), before Compose runs; the volume's content is then identical to a real deployment's. | first seen 2026-09-26 run 5 after four clean runs (a race, not a constant); the serial populate was verified to produce exactly `media -> /media` |

## Odoo Sentry (Task 12)

| Fact | Evidence |
|---|---|
| The OCA `sentry` server-wide module never initialises on this image: `initialize_sentry()` needs `sentry_enabled` in odoo.conf, and `docker/odoo.conf.template` / `entrypoint.sh` write no `sentry_*` key | kodemeio-odoo `src/oca/server-tools/sentry/hooks.py:84`, `docker/entrypoint.sh` |
| `docker/sentry_init.py` is loaded via `PYTHONSTARTUP`, which CPython applies only to interactive interpreters — so it most likely never runs in the Odoo server process; Sentry is effectively off fleet-wide | `docker/entrypoint.sh:1388` |
