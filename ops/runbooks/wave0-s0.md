# Runbook — Wave 0 S0: dangling DNS, GitHub App key, Postgres exposure (founder-gated)

Roadmap rows **0.1, 0.2, 0.3** (program roadmap, 2026-09-25).
Design: Wave 0 S0 design, 2026-09-28.
The checkers are built and tested locally against synthetic inputs; every live
step below is founder work. Scope is the **kodeme.io estate only**: zones
`kodeme.io` and `terakidz.com`, the `kod-*`/`tkz-*` manifests, and the Hetzner
projects that host kod services. Do not point any step at another estate.

CLI verbs were checked against the installed `--help` on 2026-09-28
(kctl-cf 0.12.3, kctl-hz 0.14.3, kctl-dokploy 0.18.5). Working directory for
the `uv run` lines: `kodemeio-dokploy/`. Keep every export in a private
scratch directory outside any git repo (`$S0` below), e.g.
`S0=$(mktemp -d)`.

**Hetzner profiles.** kod servers live in two Hetzner projects: `kodemeio`
(kod-prod-01, kod-prod-02) and `abcfood` (abc-prod-02 — kod postgres, Odoo,
Authentik, Mattermost; abc-prod-01 — Mailcow). The commands assume kctl-hz
profiles of those names; if yours differ, substitute them — the checker takes
`PROFILE=FILE` pairs.

---

## S0-1 — Dangling DNS (row 0.1)

Gate: **no record resolves outside our Hetzner projects.**

1. Owned-IP inventory (reads only):

   ```bash
   kctl-hz -p kodemeio --json servers list > $S0/hz-kodemeio.json
   kctl-hz -p abcfood  --json servers list > $S0/hz-abcfood.json
   kctl-hz -p kodemeio --json ips list     > $S0/ips-kodemeio.json   # floating + primary
   kctl-hz -p abcfood  --json ips list     > $S0/ips-abcfood.json
   ```

   If `ips list --json` is not a JSON list of objects with an `ip` field,
   leave those two files out — the server exports alone carry each
   server's primary IPv4 and IPv6 /64.

2. Zone exports (reads only) and the BIND backup that is the rollback:

   ```bash
   for z in kodeme.io terakidz.com; do
     kctl-cf -p kodemeio --json records list --zone $z > $S0/cf-$z.json
     kctl-cf -p kodemeio records export --zone $z -f $S0/dns-backup-$z.bind
   done
   ```

3. Check (offline):

   ```bash
   uv run python ops/scripts/s0_dns.py check \
     --desired ops/wave0/s0/dns.kod.yaml \
     --zone-export kodeme.io=$S0/cf-kodeme.io.json \
     --zone-export terakidz.com=$S0/cf-terakidz.com.json \
     --hetzner $S0/hz-kodemeio.json --hetzner $S0/hz-abcfood.json \
     --hetzner $S0/ips-kodemeio.json --hetzner $S0/ips-abcfood.json
   ```

   Exit 0 = CLEAN, 1 = findings (with a removal plan), 2 = bad input (never
   read a 2 as clean). Expected on the first run: the ~44 `kodeme.io` + 7
   `terakidz.com` records pointing at the old kod-prod-02 address as
   `dangling`, `mcp.kodeme.io` as `retired`, and every CNAME/MX to a third
   party as `unowned-target` (the allow-list ships empty on purpose).

4. Triage each `unowned-target`: a third party we really use (mail
   provider, domain verification, Cloudflare Pages…) goes into
   `ops/wave0/s0/dns.kod.yaml` under `allowed_external_targets` **with a
   reason**, committed; anything else stays in the plan. Re-run step 3 until
   the plan lists only what you intend to delete.

5. Before deleting, confirm the old kod-prod-02 IP is really gone from us:
   it must appear in **no** Hetzner export (step 1). If it is still ours
   (reassigned), stop and repoint instead of deleting.

6. Apply the removal plan exactly as printed: the `records export` lines
   (already done in step 2 — keep the files), then every `--explain` line
   (each prints what it would delete and exits 0), then the same lines with
   `--force`. CNAMEs are ordered before the names they point at.

7. **Verify:** repeat steps 1–3 → `CLEAN`, exit 0. Spot-check two deleted
   names with `dig +short <name> @1.1.1.1` → empty.

**Rollback:** `kctl-cf -p kodemeio records import --zone <zone> -f
$S0/dns-backup-<zone>.bind` (re-creates the exported records; delete any
duplicates it reports).

**Evidence for the roadmap:** the final CLEAN output + date.

---

## S0-2 — Rotate the leaked GitHub App credentials (row 0.2)

Gate: **old key rejected.**

What leaked: the **Dokploy GitHub App** used by `dokploy.kodeme.io` — its
private key and client secret appeared in a read-only Dokploy git-provider
response (`kodemeio-dsh/docs/deployment-plan.md`). If this App also serves any
control plane other than `dokploy.kodeme.io`, stop: that is outside the kod-estate
scope and needs its own decision.

🔴 Never run `kctl-dokploy git list` or `git get` — they print provider
secrets. That is how this leak happened.

1. Note the App id and the fingerprint(s) currently listed under GitHub →
   Settings → Developer settings → GitHub Apps → *the App* → **Private keys**.
   The leaked key is the one that existed at leak time; note its
   `SHA256:…` fingerprint as `OLD_FP`.

2. Rotate (GitHub UI):
   - **Generate a private key** → download the new `.pem` to `$S0/new.pem`
     (`chmod 600`).
   - **Generate a new client secret**; the old one is deleted with it.
   - Also regenerate the **webhook secret** if the App has one.

3. Update Dokploy (`dokploy.kodeme.io` → Settings → Git → the GitHub
   provider) with the new private key, client secret and webhook secret. If
   the Dokploy UI does not let you replace the key in place, use Dokploy's
   **Create GitHub App** flow to make a new App, install it on the repos the
   kod applications deploy from, repoint those applications/composes to the
   new provider, and **delete the old App** in GitHub (deleting the App
   revokes every key, secret and installation token it had). Test with
   `kctl-dokploy -p kodemeio git test <provider-id>` (boolean result; take the
   id from the Dokploy UI, not from `git list`).

4. **Delete the old private key** in the App's settings (not needed if the
   whole App was deleted).

5. **Verify** (the only live call this slice makes; to `https://api.github.com`):

   ```bash
   # if you still hold the old key file:
   uv run python ops/scripts/s0_github_app.py verify \
     --app-id <APP_ID> --new-key $S0/new.pem --old-key $S0/old.pem
   # if you do not (usual -- it leaked through tool output): copy the
   # Private keys section of the App page into $S0/listed.txt, then
   uv run python ops/scripts/s0_github_app.py verify \
     --app-id <APP_ID> --new-key $S0/new.pem \
     --old-fingerprint "$OLD_FP" --listed-fingerprints $S0/listed.txt
   ```

   `ROTATED` (exit 0) = new key → 200 with the App id, old key → exactly
   401 / old fingerprint no longer listed. `INCONCLUSIVE` or `NOT-ROTATED`
   → exit 1; do not record the row. If the App was replaced, use the **new**
   App id with `--new-key`, and the old App id with `--old-key` in a second
   run is expected to be `INCONCLUSIVE`/401 — record the old App's deletion
   from the GitHub UI instead.

   The fake used by the tests follows GitHub's REST docs, not a recorded
   response: **save this first live output** (`--json`) with the evidence so
   the fake can be checked against reality.

6. Make sure no key reached a repo (tracked content only, paths only):

   ```bash
   cd .. && repos=(); for d in kodemeio-*/; do
     git -C "$d" rev-parse --is-inside-work-tree >/dev/null 2>&1 && repos+=("$PWD/${d%/}")
   done
   uv run --project kodemeio-dokploy python \
     kodemeio-dokploy/ops/scripts/s0_github_app.py scan "${repos[@]}"
   ```

   `CLEAN` expected. It reads both the index and the working-tree copy of
   every tracked file. Header-only mentions (test fixtures that `printf` a key
   header) are listed as notes. On 2026-09-28 the local scan of 15 kod repos
   was CLEAN with 5 header-only notes.

7. `shred -u $S0/*.pem`.

**Rollback:** none for the key (a deleted key cannot be restored); if Dokploy
deploys break, fix the provider settings — never re-add the old key.

**Evidence:** the `verify --json` output + date.

---

## S0-3 — Postgres ports unpublished, delete protection on (row 0.3)

Gate: **external probe fails; API shows protection.**

### A. Unpublish the ports

The fix is committed: `deploys/instances/production/kod-infra-postgres.yaml`
and `tkz-infra-postgres.yaml` carry `env_overrides` that bind 5432/6432/9187
to `127.0.0.1` (the shared `kodemeio-postgres` compose defaults to
`0.0.0.0`). Services use `dokploy-network`; the `kctl-pg` SSH tunnel targets
the host's loopback and keeps working. Nothing is known to scrape :9187 from
outside (no monitoring stack runs); confirm before you apply.

1. Static check (offline):

   ```bash
   uv run python ops/scripts/s0_postgres.py ports
   ```

   `PASS`, exit 0.

2. Before the first external probe, from a machine **outside** Hetzner (not
   the server itself):

   ```bash
   uv run python ops/scripts/s0_postgres.py probe 138.199.213.219
   ```

   Expected today: `EXPOSED` (records the before-state).

3. Confirm which compose file the live `kod-infra-postgres` deploys from
   (Dokploy UI → the compose → General → Compose path). The manifest omits
   `compose_path`, so kctl-dokploy uses `docker-compose.yml`; the audit cited
   `docker-compose.prod.yml`. The checker verifies both, but a mismatch
   means the redeploy also switches files — set `compose_path` in the
   manifest to the live value first if they differ.

4. Redeploy in a quiet window — this restarts kod postgres, so kod Odoo
   (erp/hrms/desk), Authentik and Mattermost drop their connections for the
   restart:

   ```bash
   M=deploys/instances/production/kod-infra-postgres.yaml
   ./dokploy.sh kodemeio deploy apply -f $M --dry-run --yes       # door: prints the resolved command, runs nothing
   ./dokploy.sh kodemeio deploy apply -f $M --kctl-dry-run --yes  # kctl-dokploy's own preview against the API, no write
   ./dokploy.sh kodemeio deploy apply -f $M --yes                 # the redeploy
   ```

   `tkz-infra-postgres` targets the old kod-prod-02 (deleted); do **not**
   deploy it until the Terakidz host is settled — the committed override
   makes whatever you deploy later safe by default.

5. **Verify:** repeat step 2 → `NOT-EXPOSED` (exit 0) for 5432, 6432 and
   9187. Then `./dokploy.sh health kodemeio` and log in to one kod Odoo.

**Rollback:** revert the `env_overrides` block and redeploy (re-publishes on
all interfaces — only as a stop-gap).

### B. Delete protection

kctl-hz 0.14.3 has no server-protection verb, so the toggle is the Hetzner
Console: each server → **Protection** → enable *Delete & rebuild
protection* (Hetzner keeps both flags equal). Do this for every server in
`ops/wave0/s0/protection.kod.yaml` (kod-prod-01, kod-prod-02, abc-prod-02,
abc-prod-01). If abc-prod-01's Mailcow does not serve kodeme.io mail, remove
it from that file (commit) instead of protecting it. Optional while there:
enable protection on those servers' primary IPs too, so a future server
deletion cannot silently release an address DNS still points at (the cause
of S0-1).

**Verify** (reads only):

```bash
kctl-hz -p kodemeio --json servers list > $S0/hz-kodemeio.json
kctl-hz -p abcfood  --json servers list > $S0/hz-abcfood.json
uv run python ops/scripts/s0_postgres.py protection \
  --export kodemeio=$S0/hz-kodemeio.json --export abcfood=$S0/hz-abcfood.json
```

`PROTECTED`, exit 0. Other servers in those projects print as notes only —
read them every time: a note that is a kod server means the desired state is
stale (add it to `ops/wave0/s0/protection.kod.yaml`, commit, protect it).

**Rollback:** disable the flag in the Console (needed before any intended
delete/rebuild).

**Evidence for the roadmap:** the `probe` NOT-EXPOSED output and the
`protection` PROTECTED output, with dates.
