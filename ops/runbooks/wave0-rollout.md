# Runbook — Wave 0 rollout (founder-gated)

Takes the Wave 0 slice (built and tested locally, nothing deployed) to
production on the **kodeme.io** estate. Every step is a founder gate (spec §7,
G1–G10); each has the commands, the **verification** (spec P1–P7) and a
**rollback**. Do them in order — later steps assume earlier ones. Every
`kctl-*` command uses `-p kodemeio`; that profile is the only one any step
here reads or writes, and no other estate's host, bucket, key or job is
touched. Every command in this runbook was checked against the installed
CLIs' own `--help` on 2026-09-26 (kctl-dokploy 0.18.5, kctl-ak 0.11.0,
kctl-hz 0.14.3, kctl-cf 0.12.3, kctl-mailcow 0.13.3) — the verbs and flags
below are the ones those binaries actually accept.

Spec: Wave 0 ops design, 2026-09-26.
Roadmap rows: 0.4, 0.5, 0.6, 0.7, 0.8 in
program roadmap, 2026-09-25 stay
`specified`/`built-local` until the evidence named in each step exists.

Hosts today (Task 0, verified by `servers list` / `compose get`):
**kod-prod-01** (49.13.116.191) = the Dokploy control plane itself + Hatchet;
**abc-prod-02** (138.199.213.219) = erp/hrms/desk Odoo, Authentik, Mattermost,
kod postgres; **kod-prod-02** = dsh; **abc-prod-01** = mail. kod-prod-01 is the
default for the toolbox and Gatus only because it is already a Dokploy target —
**check its free memory before you accept that default** (this runbook adds
≈1 GB + 256 MB + 128 MB to whichever host you pick), and re-check whether the
kod production server is the right host to *avoid* once G1 names it.

---

## G1 — Choose hosts

Four `server:` fields and two hosts that are not fields. Decide, write the
choice into the file, commit:

| # | Where | Field / value | Rule |
|---|---|---|---|
| 1 | `deploys/instances/production/kod-infra-kctl.yaml` | `server:` (line ~32, already carries a `# founder:` comment) | the toolbox host; NOT the kod production server — this container holds the B2 keys |
| 2 | `deploys/instances/production/kod-infra-gatus.yaml` | `server:` (line 17, `# founder:` comment) | the monitor host; NOT the kod production server — it must still see that server fail |
| 3 | `deploys/instances/production/kod-odoo-erp-filestore-backup.yaml` | `server: abc-prod-02` | **fixed, not a choice**: the host whose Docker has the erp compose volume. Confirm it, do not move it. It is generated — edit `deploys/tenants/kod.yaml` and re-run the generator |
| 4 | `deploys/instances/production/kod-odoo-hrms-filestore-backup.yaml` | `server: abc-prod-02` | same, for hrms |
| — | Dokploy compose from `ops/authentik/remote-outpost.compose.yml` (G8) | host chosen in the Dokploy UI | the host whose Traefik fronts Dokploy + Hatchet (kod-prod-01 today) |
| — | the drill's `--docker-host` (G9) | a temporary server or Server B | never production |

Fields 1 and 2 default to `kod-prod-01`, which already runs Dokploy + Hatchet.
Budget there: ≈1 GB (toolbox) + 256 MB (Gatus) + 128 MB (outpost) — or pick
Server B. Fields 3 and 4 were read off Task 0's live `compose get` evidence
(`deploys/tenants/kod.yaml`, the comment above the erp entry) and are a fact
to confirm, not a decision to make.

**Why fields 3 and 4 carry no `# founder:` comment in their manifests** (final
review M10): those two files are generated and the header says DO NOT EDIT —
a hand-added comment is lost on the next `deploys/generate.py` run, so the
reasoning deliberately lives one level up, in `deploys/tenants/kod.yaml`'s
comment above the erp entry, and is restated in the table above. Every
non-generated `server:` (fields 1 and 2) carries its own `# founder:` comment.

- Verify: `kctl-dokploy -p kodemeio servers list` shows each chosen name
  (this is a read), then `kctl-dokploy -p kodemeio deploy validate -f
  deploys/instances/production/kod-infra-kctl.yaml` → `OK` (same for
  `kod-infra-gatus.yaml`).
- Rollback: revert the commit. Nothing is deployed by this gate.

## G2 — Backblaze B2 bucket, keys, lifecycle (HARD PRECONDITION for any mirror run)

In the B2 console (master key, laptop only):
1. Bucket **`kod-prod-backup`**, private, SSE-B2 on, lifecycle
   `daysFromHidingToDeleting: 30`, `daysFromUploadingToHiding: null`.
2. Mirror key `kod-prod-backup-mirror`: bucket-scoped, capabilities
   `listBuckets, listFiles, readFiles, writeFiles` — **no `deleteFiles`**.
3. Read-only key `kod-prod-backup-readonly`: `listBuckets, listFiles, readFiles`.
4. Store all three in 1Password vault `Kodemeio`
   (`b2: kod-prod-backup (MASTER key)`, `b2: kod-prod-backup-mirror`,
   `b2: kod-prod-backup-readonly`).

- Verify (P3) — the hard-delete test MUST fail with `401 unauthorized` using
  the MIRROR key (run from the laptop; `rclone` needs no local install):
  ```bash
  export RCLONE_CONFIG_B2_ACCOUNT=<mirror keyID> RCLONE_CONFIG_B2_KEY=<mirror appKey>
  R() { docker run --rm -i -e RCLONE_CONFIG_B2_TYPE=b2 -e RCLONE_CONFIG_B2_ACCOUNT \
          -e RCLONE_CONFIG_B2_KEY -v "$PWD:/w" rclone/rclone:1.75.1 "$@"; }

  printf 'g2 probe\n' > b2-probe.txt
  R copyto /w/b2-probe.txt b2:kod-prod-backup/_g2/probe.txt        # MUST print nothing and exit 0
  R deletefile b2:kod-prod-backup/_g2/probe.txt --b2-hard-delete   # MUST exit non-zero, "401 unauthorized"
  ```
  **`--b2-hard-delete` is the whole point of the test.** Without it B2 only
  *hides* the file, and hiding is a write — the mirror key's `writeFiles`
  succeeds and the test passes while the protection is absent. Only a hard
  delete exercises `deleteFiles`.
  Then remove the probe with the MASTER key (laptop-only item), also with
  `--b2-hard-delete`, and confirm the `_g2/` prefix is empty. No 401 on the
  mirror delete ⇒ STOP; do not deploy the toolbox.
- Rollback: delete the keys/bucket in the console (nothing depends on them yet).

## G3 — Healthchecks.io, Telegram, SMTP

1. Healthchecks.io (Hobbyist, free — 20 checks) project `kodemeio`;
   integrations: **Telegram** (bot from BotFather via @BotFather, alert chat id)
   and **email** to `ALERT_TO`. Both are required: D5's whole point is hearing
   about an *absence*, from outside Hetzner.
2. Four checks. The name MUST equal the job name — `hc.sh` pings the check by
   URL, and `jobrun`'s email already uses the name, so a mismatch means two
   naming systems for one job. The `gatus` check is the dead-man for Gatus
   itself (G7):

   | Check name | Period | Grace |
   |---|---|---|
   | `kod-offsite-mirror` | 2 h | 1 h |
   | `kod-hz-fresh` | 1 d | 2 h |
   | `kod-offsite-fresh` | 1 d | 2 h |
   | `gatus` | 1 min | 5 min |

   Grace for the mirror is deliberately one *period* (not minutes): the first
   runs are a seed and legitimately `partial`, which pings nothing.
3. Kod SMTP mailbox for alerts, on Mailcow:
   ```bash
   kctl-mailcow --profile kodemeio mailboxes add alerts kodemeio.io \
       --password "$(op read 'op://Kodemeio/kod-alerts-smtp/password')" \
       --name "kod alerts" --quota 1024
   ```
   (`--profile` long form, not `-p`: `mailboxes add` defines its own `-p` as
   `--password`, so the short form is ambiguous here.) Then 1Password items for
   the bot token, the chat id, the SMTP password and the four ping URLs —
   `~/.config/kodemeio/config.yaml` carries them into the toolbox env as
   `HC_KOD_OFFSITE_MIRROR`, `HC_KOD_OFFSITE_FRESH`, `HC_KOD_HZ_FRESH`,
   `HC_GATUS_URL` + `ALERT_TO` + `SMTP_*`.

- Verify: `curl -fsS <ping-url>` turns a check green once (then it waits for
  real pings); Healthchecks → each check → "Integrations" shows both Telegram
  and email; a `/fail` ping (append `/fail` to the URL) actually messages both.
- Rollback: pause or delete the checks, delete the mailbox.

## G4 — Toolbox image, deploy, schedules (row 0.5)

Preconditions (R8, extended by final review I5): **all five** must hold before
the three schedules are flipped to `enabled: true` — G2 passed; the
`kod_odoo_hrms` dump fresh again (Task 0: one dump, 223 h old — fix kod
postgres `BACKUP_DATABASES`/cron first); erp/hrms restic repos exist (G5);
**G5 + G6 have actually deployed the backups the freshness jobs check** (the
erp/hrms filestore jobs and the `kodemeio-authentik-backup` bucket + sidecar);
and **the Mattermost `mm-files-backup` sidecar is deployed** (the Mattermost
redeploy). Until all of them hold, the schedules stay `enabled: false` — the
same list is in `kod-infra-kctl.yaml`'s comment and is pinned by
`deploys/tests/test_kod_schedule_gates.py`.

1. Merge kodemeio-skills to `main` (image builds on `docker/**` changes); read the
   new `sha-<short>` tag from GHCR and replace `sha-PENDING` in
   `kodemeio-skills/compose/toolbox-kod.yml`.
2. **Pre-deploy gate (R9)** — in kodemeio-skills:
   ```bash
   DEPLOY_CHECK=1 bash tests/test_toolbox_kod.sh      # must end ALL PASS (fails while sha-PENDING)
   ```
   Also add a CI job that runs the shell suites —
   `test_jobrun.sh`, `test_kod_jobs.sh`, `test_b2_generalised.sh`,
   `test_toolbox_kod.sh` (and `test_b2_mirror.sh`) — next to the existing
   checks: `.github/workflows/ci.yml` runs no shell suite at all today, so
   nothing there would catch a broken job script (final review M4).
3. Fill `deploys/env/production/.env.kod-infra-kctl` from the example (1Password), then
   ```bash
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-kctl.yaml --dry-run
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-kctl.yaml
   ```
   (the verify phase fails by design — no HTTP surface; see the manifest note).
   This same apply **creates the three schedules**, all with `enabled: false`
   (`schedules:` in the manifest) — a compose service with no schedules is a
   container that does nothing.
   ```bash
   kctl-dokploy -p kodemeio compose search --name kod-infra-kctl   # the compose + id
   kctl-dokploy -p kodemeio compose get <compose-id>              # note the service id for step 4
   ```
4. Preview the schedules without writing, then run each one by hand and READ
   the output. Registration is not verification — a schedule row proves nothing:
   ```bash
   kctl-dokploy -p kodemeio deploy schedules-diff -f deploys/instances/production/kod-infra-kctl.yaml
   kctl-dokploy -p kodemeio schedules list <compose-id>       # three rows, all enabled=false
   kctl-dokploy -p kodemeio schedules run <schedule-id>       # mirror first, then hz-fresh, then offsite-fresh
   kctl-dokploy -p kodemeio schedules history <schedule-id>   # the RESULT= / SUMMARY lines
   kctl-dokploy -p kodemeio compose service-logs <compose-id> # the same lines live
   ```
   **Expected on this first run — do not treat these as defects** (final review
   I5; they are the correct alarm for something not deployed yet, and they
   clear by themselves as G5/G6 land):

   | Symptom | Cause at this point | Clears when |
   |---|---|---|
   | mirror `RESULT src=kodemeio-authentik-backup status=FAILED reason=cannot-list-source` | the `kodemeio-authentik-backup` bucket does not exist yet | G6 provisions it |
   | mirror `partial=N` and **no Healthchecks ping at all** | initial seed still catching up (600 s budget per run, resumes next) | the seed finishes — a withheld ping is the design, not a failure |
   | freshness `STALE` for `kodemeio-postgres-backup/kod_odoo_hrms/` | the hrms dump is still stale (precondition above) | the kod postgres dump is fixed |
   | freshness `STALE` for `kodemeio-odoo-filestore/kod-odoo-{erp,hrms}/snapshots/` | those filestore jobs are not deployed yet | G5 |
   | freshness `STALE` for `kodemeio-authentik-backup/media/`, `kodemeio-mattermost-backup/mattermost/files/` | those sidecars are not deployed yet | G6 / the Mattermost redeploy |
   | P1 (below) cannot pass yet | most prefixes are still empty | G5 + G6 |

   The general rule for this slice: **read `reason=`/`RESULT=` in the output and
   match it against the table before changing anything.** A red you cannot find
   in this table is a real finding.
5. Only once the five preconditions above hold, flip `enabled: true` on the
   three schedules in the manifest, commit, re-apply, and confirm Dokploy agrees:
   `kctl-dokploy -p kodemeio deploy schedules-diff -f …kod-infra-kctl.yaml` → no diff.
   `deploys/tests/test_kod_schedule_gates.py` fails by design on that flip until
   the enabled name is added to its `ENABLED_BY_EXCEPTION` with the evidence —
   that is the review trail, not a test to delete.

- Verify — **P1** (run **after G5 and G6 have deployed**, not at G4) every
  inventory prefix has objects on B2 (with the read-only key, from the laptop):
  ```bash
  R lsf b2:kod-prod-backup/kodemeio-postgres-backup/kod_odoo_erp/
  R lsf b2:kod-prod-backup/kodemeio-odoo-filestore/kod-odoo-erp/snapshots/
  ```
  **P2** `kod-hz-fresh` and `kod-offsite-fresh` each print `RESULT=ok` once
  (read the run, not the green schedule row). **P4** Healthchecks shows all four
  checks up. **R10:** three restic backups share 22:20 UTC on abc-prod-02
  (distinct repos, no lock conflict) — stagger them only if the first runs show
  I/O contention.
- Rollback: set `enabled: false` and re-apply (or stop the compose); B2 keeps
  everything already copied.
- Evidence → row **0.5**: B2 listing + the two green freshness runs + Healthchecks screenshot.

## G5 — erp/hrms filestore backups (row 0.4)

```bash
kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-odoo-erp-filestore-backup.yaml --dry-run
kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-odoo-erp-filestore-backup.yaml
kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-odoo-hrms-filestore-backup.yaml --dry-run
kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-odoo-hrms-filestore-backup.yaml
```
`min_files` ships as a **seed of 1** — a number that guards nothing yet, so the
measurement is part of the gate, not an afterthought:

```bash
# 1. run the one-shot backup by hand (kodemeio-odoo/docs/admin/filestore-backup.md)
kctl-dokploy -p kodemeio schedules list <kod-odoo-erp-filestore-backup-compose-id>
kctl-dokploy -p kodemeio schedules run <erp-schedule-id>
kctl-dokploy -p kodemeio compose service-logs <compose-id>      # read "files=N" from the guard
# 2. set min_files to 90 % of N in deploys/tenants/kod.yaml (both entries), then
uv run python deploys/generate.py --tenant kod
# 3. commit the generated manifests + tenants/kod.yaml, and re-apply both
```
Keep autoDeploy OFF for these two — a regenerated manifest must not ship itself.
- Verify: `kctl-hz -p kodemeio s3 freshness kodemeio-odoo-filestore --prefix kod-odoo-erp/snapshots/ --max-age-hours 8`
  (and `kod-odoo-hrms/snapshots/`) exit 0; a `restic check` on each repo; the
  erp and hrms filestore volumes are the ones Task 0 read off the live composes
  (`compose-connect-online-firewall-uowe1h_odoo-filestore`,
  `compose-generate-digital-alarm-z4bb6z_odoo-filestore`) and the guard refused
  a wrong volume loudly on the first run.
- Rollback: stop the backup compose (read-only mount; Odoo untouched).

## G6 — Authentik media backup sidecar (row 0.4)

1. Confirm the LIVE `AUTHENTIK_STORAGE_BACKEND` on `kod-infra-authentik` (name +
   value of that one variable only, never a full env dump). `s3` ⇒ media lives in
   Authentik's own bucket (`AUTHENTIK_STORAGE__MEDIA__S3__BUCKET_NAME`): add that
   bucket to `kod-offsite-mirror.sh` + the inventory instead, and the sidecar's
   `media_in_s3` skip is expected. `file` ⇒ the sidecar is the backup.
2. Provision bucket **`kodemeio-authentik-backup`** (`kctl-hz -p kodemeio s3 mb kodemeio-authentik-backup`) and its S3 key in the Authentik compose env.
   **Ordering (final review I3):** these four `S3_BACKUP_*` values did not exist
   before this step, so `docker-compose.prod.yml` renders them as `${…:-}` — a
   redeploy of the LIVE SSO stack for any other reason cannot fail at
   compose-render time. The price is that an unset pair means the sidecar
   refuses to run: `backup.sh` logs
   `FATAL: refusing to run -- required setting(s) empty: S3_BUCKET …` and exits
   **78**. Seeing that line in the Authentik logs before this step is expected;
   seeing it AFTER the values are set is a real defect. Same for
   `kodemeio-mattermost-backup`'s sidecars.
3. Redeploy Authentik (kodemeio-authentik `docker-compose.prod.yml`, which carries the
   `backup` service); check the first `RESULT=ok` in its log.
4. **Mattermost files — confirm the premise this backup rests on** (final review
   I2). The `mm-files-backup` sidecar tars the `mm-data` Docker volume, which is
   only the attachment store if Mattermost's file driver is `local`; Task 0
   could not read the live value and the compose default is `amazons3`.
   Name + value of that one variable only, never a full env dump:
   ```bash
   kctl-dokploy --json -p kodemeio compose env list <mm-compose-id> \
     | jq '{MM_FILESETTINGS_DRIVERNAME, MM_FILESETTINGS_AMAZONS3BUCKET}'
   ```
   - `local` (or unset with the volume in use) ⇒ the volume IS the attachment
     store; continue below.
   - `amazons3` ⇒ `mm-data` holds nothing: the sidecar refuses and logs
     `RESULT=FAILED files=<n>` (`FILES_MIN_FILES` floor) instead of uploading an
     empty tar, **and the real bucket must be added to the offsite mirror
     instead** — add a `b2_sync` line for it and an inventory item, exactly the
     `authentik-media` branch above. Do not "fix" this by removing the floor.
5. **Mattermost files — deploy the sidecar and set the real floor.** Redeploy
   mattermost so `mm-files-backup` starts; read the first
   `RESULT=ok files=N`, then set `FILES_MIN_FILES` to 90 % of N in the compose
   env (it ships as a seed of 1, exactly like the odoo filestore `min_files`) and
   redeploy. `kod-hz-fresh`/`kod-offsite-fresh` alarm on
   `kodemeio-mattermost-backup/mattermost/files/` until the first upload lands.
- Verify: `kctl-hz -p kodemeio s3 freshness kodemeio-authentik-backup --prefix media/ --max-age-hours 30` exit 0,
  and the same for `kodemeio-mattermost-backup --prefix mattermost/files/ --max-age-hours 30`.
- Rollback: remove the `backup` service env / scale it to 0; SSO is unaffected.

## G7 — Gatus + forced outage (row 0.7)

1. Fill `.env.kod-infra-gatus` (Telegram, SMTP, `HC_GATUS_URL`), then
   ```bash
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-gatus.yaml --dry-run
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-gatus.yaml
   ```
2. Status UI stays private (no router). Publishing it later = domain block +
   router behind Authentik forward-auth.
3. Gate checks `gate-dokploy` and `gate-hatchet` alert until G8 is done — expected.
   Flip the three `llm-*` endpoints to `enabled: true` in the same commit that ships LiteLLM.
- Verify (P5): forced outage — temporarily point one endpoint at a closed port
  (e.g. `https://erp.kodeme.io:9/`), deploy, receive Telegram **and** email within
  ~3 min, revert, receive "resolved". Stop the Gatus compose for 6 min → the
  `gatus` Healthchecks check alerts (dead-man). Locally: `uv run pytest ops/monitoring/gatus/tests -q`.
- Rollback: stop the compose.
- Evidence → row **0.7**: the two alert messages + resolved messages.

## G8 — Admin gates (row 0.8) — can lock you out; do it slowly

1. **Break-glass first**: `ssh -L 3000:localhost:3000 root@kod-prod-01`, open
   `http://localhost:3000`, log in. Do not continue until this works.
2. Resolve Hatchet's mode: from a real host, `curl -s -o /dev/null -w '%{http_code}' https://auth.kodeme.io/outpost.goauthentik.io/ping`.
   204 ⇒ domain-level via the public URL is possible (`AUTHENTIK_OUTPOST_URL=https://auth.kodeme.io`);
   anything else ⇒ remote outpost (default).
3. Authentik (`kctl-ak -p kodemeio`): Proxy Providers in forward-auth single-app
   mode (`forward_single` is the CLI default — passing it explicitly documents
   the intent). Each `create` prints the new row; read the ids back with the
   matching `list` verb rather than trusting the order:
   ```bash
   kctl-ak -p kodemeio providers proxy create "Dokploy" https://dokploy.kodeme.io --mode forward_single
   kctl-ak -p kodemeio providers proxy create "Hatchet" https://hatchet.kodeme.io --mode forward_single
   kctl-ak -p kodemeio providers proxy list                       # note both provider ids
   kctl-ak -p kodemeio apps create "Dokploy" dokploy --provider <dokploy-provider-id> --launch-url https://dokploy.kodeme.io
   kctl-ak -p kodemeio apps create "Hatchet" hatchet --provider <hatchet-provider-id> --launch-url https://hatchet.kodeme.io
   kctl-ak -p kodemeio outposts create kod-prod-01-proxy --type proxy \
       --providers <dokploy-provider-id>,<hatchet-provider-id>
   ```
   A provider with no policy lets **anyone who can authenticate** into the
   Dokploy UI — bind both applications to an admins-only policy, and test it:
   ```bash
   kctl-ak -p kodemeio policies create kod-admins-only --type expression \
       --expression 'return ak_is_group_member(request.user, name="authentik Admins")'
   kctl-ak -p kodemeio policies list                  # note the policy uuid
   kctl-ak -p kodemeio policies test <policy-uuid> --user <akadmin-pk>   # expect pass
   kctl-ak -p kodemeio policies bind <policy-uuid> --target <dokploy-app-uuid>
   kctl-ak -p kodemeio policies bind <policy-uuid> --target <hatchet-app-uuid>
   kctl-ak -p kodemeio policies bindings              # both bindings listed
   ```
   Copy the outpost token (Authentik → Outposts → View deployment info) into
   1Password `authentik-outpost-kod-prod-01`.
4. Remote outpost: create a Dokploy compose on kod-prod-01 from repo
   `kodemeio-dokploy`, compose path `ops/authentik/remote-outpost.compose.yml`,
   env `AUTHENTIK_OUTPOST_TOKEN` (Dokploy UI env editor), autoDeploy off.
   Verify `kctl-ak -p kodemeio outposts health <id>` is healthy. (Memory: +128 MB on kod-prod-01.)
5. DNS: `kctl-cf -p kodemeio records create --zone kodeme.io --type A --name dokploy-api --content 49.13.116.191 --no-proxied`.
6. Traefik file — read before you write. `dokploy.yml` in
   `/etc/dokploy/traefik/dynamic/` on kod-prod-01 is the live Dokploy dynamic
   config (Task 0 read it; it defines `dokploy-router-app`,
   `dokploy-router-app-secure` and `dokploy-service-app` → `http://dokploy:3000`).
   Read it again to confirm those names still exist — the gate file routes to
   `dokploy-service-app`, and a rename would leave the gated routers pointing at
   nothing:
   ```bash
   kctl-dokploy -p kodemeio traefik file get dokploy.yml --server kod-prod-01
   kctl-dokploy -p kodemeio traefik file put dokploy-admin-gate.yml --from ops/traefik/dokploy-admin-gate.yml --server kod-prod-01 --explain
   kctl-dokploy -p kodemeio traefik file put dokploy-admin-gate.yml --from ops/traefik/dokploy-admin-gate.yml --server kod-prod-01
   ```
   `put` reads the file back after writing (the CLI does that itself) — that
   read-back is the confirmation, not the exit code.
7. Switch kctl-dokploy's `kodemeio` profile URL to `https://dokploy-api.kodeme.io`
   in `~/.config/kodemeio/config.yaml`; check `kctl-dokploy -p kodemeio servers list` works.
   Change every GitHub webhook of the kodemeio Dokploy to the `dokploy-api` host.
8. Hatchet: set `HATCHET_UI_MIDDLEWARES=authentik-hatchet@docker` (and
   `AUTHENTIK_OUTPOST_URL` if step 2 chose public) and redeploy Hatchet.
- Verify (P6): `bash ops/scripts/check-admin-gates.sh` → exit 0, every line `OK`
  (`GATE <url> <status> <location-host> OK`). The three `llm.kodeme.io` lines
  FAIL until LiteLLM ships — until then run with a targets file, which is one
  `<url> <whitespace> <expectation>` per line:
  ```bash
  cat > /tmp/kod-gates-pre-llm.txt <<'EOF'
  https://dokploy.kodeme.io/                            expect-gated
  https://dokploy.kodeme.io/api/trpc/                   expect-gated
  https://dokploy-api.kodeme.io/api/auth/get-session    expect-404
  https://dokploy-api.kodeme.io/                        expect-404
  https://hatchet.kodeme.io/                            expect-gated
  https://hatchet.kodeme.io/api/v1/meta                 expect-401-or-403
  https://dsh.kodeme.io/                                expect-gated
  EOF
  bash ops/scripts/check-admin-gates.sh --targets /tmp/kod-gates-pre-llm.txt
  ```
  Once LiteLLM ships, run plain `bash ops/scripts/check-admin-gates.sh` (the
  built-in list adds `llm.kodeme.io/ui` and `llm.kodeme.io/key/list`).
  Browser: dokploy.kodeme.io → Authentik login → Dokploy; and
  `dokploy-api.kodeme.io/` must 404 (an ungated host with a login form is the
  whole thing D8 removes). Gatus gate checks (`gate-dokploy`, `gate-hatchet`)
  go green. Locally: `bash ops/traefik/tests/test_gates.sh`.
- Rollback: `traefik file put` an empty `http: {}` for `dokploy-admin-gate.yml`
  (the original router takes over), `HATCHET_UI_MIDDLEWARES=hatchet-local-auth@docker`,
  profile URL back to dokploy.kodeme.io. Break-glass tunnel works throughout.
- Evidence → row **0.8**: `check-admin-gates.sh` output.

## G9 — First timed drill (row 0.6)

A temporary server you create and destroy yourself (Hetzner create/delete is
never the agent's hands — spec §8). [restore-drill.md](restore-drill.md) has the
environment setup; from the laptop, with the read-only B2 key exported:

```bash
D=$(date -u +%F)
bash ops/drills/odoo/drill-odoo.sh --db-name kod_odoo_erp \
    --dump src:kod-prod-backup/kodemeio-postgres-backup/kod_odoo_erp/latest \
    --restic-repo "s3:${RCLONE_CONFIG_SRC_ENDPOINT}/kod-prod-backup/kodemeio-odoo-filestore/kod-odoo-erp" \
    --snapshot latest --odoo-image "$ODOO_IMAGE" \
    --docker-host ssh://root@<drill-host> --out "ops/drills/results/${D}-kod_odoo_erp.json"
# …then kod_odoo_hrms, kod_odoo_desk — those two have no Sales module, so the
# row-count cross-check must be pointed at a model they DO have:
#   --orders-model res.partner   (in `base`, so always present; keeps a check)
#   --no-orders                  (turns the check off; a skipped check is one
#                                 you no longer have — prefer the line above)
# Without either, the drill now SKIPS the check itself and says so in
# validation.orders.skipped instead of failing the run (final review I4); see
# restore-drill.md's "which databases" table. And:
bash ops/drills/authentik/drill-authentik.sh \
    --dump src:kod-prod-backup/kodemeio-postgres-backup/authentik/latest \
    --media src:kod-prod-backup/kodemeio-authentik-backup/media/latest \
    --docker-host ssh://root@<drill-host> --out "ops/drills/results/${D}-authentik.json"
```
Both scripts exit 0 only on `status: ok`; on failure the JSON still carries
`status`, `failed_step`, `failed_detail` and every timing up to it. Before any
of this, the same two scripts must have passed locally:
`bash ops/drills/tests/test_drill_odoo.sh` and
`bash ops/drills/tests/test_drill_authentik.sh` (both `ALL PASS`) — those are
W6–W8 and they are what proves the drill itself is not the broken part.
- Verify (P7): one results row per run in `ops/drills/results/README.md`, built
  from the JSON (never hand-typed), with a measured RPO and RTO. A drill that
  fails is still evidence — record it as failed and fix the cause.
- Rollback: n/a (every step reads B2 and writes only throwaway containers);
  destroy the temporary server afterwards — production data sat on its disk.
- Evidence → row **0.6**: the committed results JSON + the row.

## G10 — Supabase export

[supabase-export.md](supabase-export.md): dry run first (the script defaults to
`--dry-run`), then the real export, then verify the `manifest.json` and the
object list on B2, then one sandbox restore, then record the row. Fill in the
provider backup tier table (H5) from the Supabase dashboard while you are there.
The DB URL is founder-held and must never be pasted into a shell that logs it.

---

## Roadmap rows and evidence

Rows 0.4–0.8 in
program roadmap, 2026-09-25. Each
stays `specified`/`built-local` **until the evidence below exists** — this
runbook being written is not evidence, and neither is a green deploy.

| Row | Moves to `staged`/`operational` when | Evidence to leave behind |
|---|---|---|
| 0.4 (backups complete) | G5 + G6 deployed and fresh; one `restic check` clean per filestore repo | the two `s3 freshness` outputs + the `restic check` output |
| 0.5 (offsite copy) | G2 401 test passed; both freshness jobs `RESULT=ok` once; all four Healthchecks up | B2 listing + the two run outputs + a Healthchecks screenshot |
| 0.6 (restore proven) | G9 drill ran on a non-production host and its JSON is committed | link to `ops/drills/results/<date>-<target>.json` + the table row |
| 0.7 (uptime + alerting) | G7 forced outage delivered Telegram *and* email, then resolved | the four messages (trigger + resolve, both channels) |
| 0.8 (admin gates) | G8 `check-admin-gates.sh` exits 0 against production | that output, plus the break-glass tunnel confirmed working |

Known follow-ups outside this slice: Odoo `sentry_init.py` is loaded via
`PYTHONSTARTUP` and so likely never runs in the server process (Task 12 report) —
fix before deploying GlitchTip on kod; the scrubber reaches production only after
a manual kodemeio-odoo **base** image build.
