# kod-ovh-01 — OVHcloud VPS for kod/Kodeme capacity

Status: **active**, provisioned 2026-10-05. Registered in Dokploy as `kod-ovh-01`
(server id `W-sYqWjxLu9cAhp41pwpA`). Purpose: kod/Kodeme production capacity
(COST_MODEL.md §4 trigger — offload `abc-prod-02` instead of rescaling).

## Identity

| | |
|---|---|
| OVH service | `vps-04bdf8d7.vps.ovh.net` (displayName `kod-ovh-01`) |
| Account | OVHcloud Canada subsidiary → API base `https://ca.api.ovh.com/1.0` |
| Model | VPS-3 2027 — 6 vCPU / 12 GB RAM / 100 GB NVMe |
| Location | Frankfurt (DE), OpenStack zone `os-de2` |
| IPv4 / IPv6 | `57.131.162.248` / `2001:41d0:701:1100::e711` (gw `2001:41d0:701:1100::1`) |
| OS / Docker | Ubuntu 24.04.5 LTS (UTC); Docker 28.5.0 (installed by Dokploy `servers setup`) |

## Access

- `ssh -i ~/.ssh/id_rsa_kodeme root@57.131.162.248` — the estate key; the same
  keypair is registered in Dokploy as `kodeme-ssh` (`sx6ML42xnSGcIuaIZTMo-`).
- Operator credentials: `~/.config/ovh/api.env` (0600, never committed) —
  `OVH_APPLICATION_KEY` / `OVH_APPLICATION_SECRET` / `OVH_CONSUMER_KEY`, plus
  the same three values in the 1Password **`OVH`** item in the Kodemeio vault
  (created 2026-10-10, which closes the earlier TODO). The consumer key in both
  places is the one scoped `GET /me`, `GET /vps/*`, `GET /ip/*`, `POST /ip/*`,
  `PUT /ip/*`. Two earlier keys that could not reach the reverse API stay
  recorded in that item's notes and can be revoked.
- OVH API requests sign `$1$` + SHA1(`AS+CK+METHOD+URL+BODY+TIMESTAMP`) and send
  `X-Ovh-Application` / `X-Ovh-Consumer` / `X-Ovh-Signature` / `X-Ovh-Timestamp`.
  A credential can be requested programmatically with `POST /auth/credential`
  (signed with an *empty* consumer key); it returns a `validationUrl` a human
  approves and a consumer key that becomes valid on approval.
- **Reverse DNS (PTR)** is set with `POST /ip/{ip}/reverse`
  (`{"ipReverse": "<ip>", "reverse": "<host>"}`). Do not use
  `/vps/{service}/reverse` (404 — the endpoint does not exist) and do not use
  `PUT /vps/{service}/ips/{ip}` — the manager's internal API uses it, but the
  public `/1.0` API answers `403 not implemented`. A VPS IP does appear in the
  IP API (`GET /ip/{ip}` → `type: vps`). `~/.local/bin/ovh-set-ptr.py` wraps the
  signing + call and is idempotent:
  `ovh-set-ptr.py --ip 57.131.162.248 --reverse mail.kodeme.io`.

## Hardening (Hetzner parity, plus documented deltas)

- **sshd**: `PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no` —
  `/etc/ssh/sshd_config.d/00-kod-hardening.conf`. The `00-` prefix is
  load-bearing: sshd takes the first value across drop-ins and
  `50-cloud-init.conf` sets `PasswordAuthentication yes`.
- **ufw**: active, default deny incoming / allow outgoing, allow `22`, `80`,
  `443` (+ `ipv6`-icmp; ICMP echo is allowed by ufw's stock `before.rules`).
  Delta vs Hetzner: their hosts rely on cloud firewall `firewall-1`; OVH VPS has
  no edge firewall, so the host carries it. `firewall-1`'s service ports (mail
  25/465/587/993, DNS 53, rsync 873, rustdesk 21115-21119, redis 6379, …) are
  **not** opened here until such a service actually lands on this host.
- **fail2ban**: `sshd` jail — 5 retries / 10 min window → 1 h ban, systemd
  backend. Delta vs Hetzner (they run none): port 22 is world-scanned and this
  host has no edge firewall.
- **unattended-upgrades**: enabled, daily (same as Hetzner hosts).
- Verified from `kod-hzc-01` (2026-10-05): 22 open; 80/443 refused (allowed, no
  listener); 3000/6379 dropped.

## Provisioning recipe (OVH API) — read before ever rebuilding

Reinstalls are **not** side-effect free: each one emails the OVH account a new
console password (7-day secret link) and invalidates the old one. Key access
survives a rebuild only if a key is injected at install time.

1. Image id: `GET /vps/{svc}/images/available` (Ubuntu 24.04 was
   `c480cf0a-cfe3-40fb-9e21-b4eff980cfa4` on 2026-10-05).
2. `POST /vps/{svc}/rebuild` with **either** `publicSshKey` (inline key
   material) **or** `sshKey` (a name from `/me/sshKey`) — the two are mutually
   exclusive (400 `sshKey and publicSshKey are incompatible`). `doNotSendPassword`
   is only honoured together with `sshKey`, and combined with a missing key it
   disables password auth *and* installs no key ⇒ lockout (observed). The recipe
   that worked on the 2027 line: `publicSshKey` **plus** `postInstallScript`
   (runs as root on first boot; installs the key into
   `/root/.ssh/authorized_keys` and sets the hostname; log:
   `/var/log/ovh-postinstall.log`).
3. Rename: `PUT /vps/{svc}` `{"displayName": "kod-ovh-01"}`.
4. Automated backup already enabled (rotation 1, ~15:28 host-local schedule);
   snapshot option disabled.

## Dokploy

```bash
kctl-dokploy -p kodemeio servers create --name kod-ovh-01 \
  --ip 57.131.162.248 --ssh-key sx6ML42xnSGcIuaIZTMo- --username root
kctl-dokploy -p kodemeio servers setup W-sYqWjxLu9cAhp41pwpA   # Docker etc.
kctl-dokploy -p kodemeio servers validate W-sYqWjxLu9cAhp41pwpA
```

- `servers setup`'s HTTP call can read-timeout client-side while the remote
  setup continues — confirm with `servers validate` (Docker / nixpacks /
  railpack / swarm / dokploy-network present, `privilegeMode: root`).
- kctl-dokploy 0.18.5 shipped a stale `/server.create` payload (missing
  `description` / `serverType` / `sshKeyId` keys → Dokploy zod 400). Fixed
  2026-10-05 in `kodemeio-skills` (`packages/kctl-dokploy`,
  `commands/servers.py` + payload-contract tests); the installed CLI was
  rebuilt from source. Ensure that fix is present before repeating this step.

## Fleet naming (founder, 2026-10-05)

Scheme `<tenant>-<provider>-<nn>`: `kod-hzc-01` (Hetzner Cloud; Dokploy entry
renamed from `kod-prod-01`), `kod-ovh-01` (this host). Hetzner-side names
(kctl-hz) are unchanged for now. Rename fallout handled: manifests updated to
`server: kod-hzc-01` (`kod-infra-{alloy,gatus,kctl,plausible,shlink}`), each
re-validated; prose in older runbooks still says `kod-prod-01`.

## Current workload and backup records

Production records now include PostgreSQL, Odoo ERP/Desk and the Kodeme operator
gateways on this host. See the instance manifests and the current
[OMP migration runbook](omp-ovh-migration.md) for the later migration evidence.
The initial no-workload snapshot from 2026-10-05 is superseded.

Native database and volume backup jobs are managed directly in Dokploy. The
installed manifest backup phase does not forward all fields required to create
PostgreSQL compose backups, including the database user and service/database
selection. Manifest schedule/retention metadata alone does not prove that a job
can be recreated. Inspect the existing native jobs before a replay; successful
backup creation is separate from archive/restore acceptance.

## Mailcow — mail.kodeme.io (2026-10-10)

`kod-infra-mailcow` (`YWH3SbofhTieHaJZld2jG`, `apps` / `production`) serves
kodeme.io from this host. Mailcow itself is installed at
`/opt/mailcow-dockerized`, pinned to the latest stable tag (`2026-09a`); the
compose project only *includes* that path. Dokploy re-clones the repo on every
deploy, so mailcow's `data/` must never live inside the clone — when it did,
vhosts ended up bind-mounted to a deleted inode and `/api/v1` answered 404.

Provisioning and the production gate live in the mailcow repo:
`make provision` (idempotent phases) and `make verify` (health, containers,
queue, LE certs, published DKIM, FCrDNS, real admin-UI login, end-to-end
delivery with a DKIM signature). PTR `57.131.162.248 → mail.kodeme.io` is live
and forward-confirmed. Volume backups for the two volumes that cannot be
rebuilt — `vmail-vol-1` (mail spool) and `mysql-vol-1` (mailcow database) — are
declared in the
[instance manifest](../../deploys/instances/production/kod-infra-mailcow.yaml).

## Kodeme operator agents — 2026-10-07

Tri authorized new JARVIS and SENTINEL setup on this host. The independently
verified deployments are `kod-infra-hermes-jarvis-tera`
(`4nr-Gy9QaWV0ApEcVrp3H`) and `kod-infra-hermes-sentinel-tera`
(`iKyt78Oq8Gmp29yeOkhIN`), under `apps` / `production`. Their matching
[manifests](../../deploys/instances/production/kod-infra-hermes-jarvis-tera.yaml)
and [SENTINEL manifest](../../deploys/instances/production/kod-infra-hermes-sentinel-tera.yaml)
use `kodemeio-hermes:v2026.8.31`, pinned upstream commit
`29112bef099274229cadff79cdff7bf7b99c4b77`, verified local build context
`/opt/hermes-upstream/v2026.8.31`. Their deployment source repository is
`tgunawandev/kodemeio-hermes`.
JARVIS uses source branch `agent/kodeme-weknora-mcp`; SENTINEL uses
`agent/kodeme-monitoring-weknora`, preserving its monitoring configuration.
Both have separate read-only WeKnora connections through
`weknora-mcp.kodeme.io`; their endpoint credentials remain private.
Auto-deploy is disabled. Never enable it by relying on the manifest alone:
this installed deploy orchestrator initially left the new compose API flags true;
explicit `compose update --no-auto-deploy` and re-reading the API corrected them.

Each gateway is capped at 1 CPU / 1 GiB and has a separate state volume. SENTINEL
alone adds `kod-operations-read-sentinel` (0.5 CPU / 256 MiB), image
`kodemeio-operations-read:d2a60af8c1dd`, source
`/opt/kodemeio/operations-read/d2a60af8c1dd`. Its private policy/provider/bearer
mounts are under `/etc/kodemeio/operations-read-kodemeio/`; the bearer/provider
files are mode 0600, owned by uid 10000. No Docker socket or published ports.
Never print or commit those files or private deployment env inputs.

Two native volume-backup jobs use the verified actual volume names in the
manifests, not the requested Compose project-name guesses. They retain 14 copies
in `kodemeio-s3-backups` at 04:00 / 04:15 UTC and stop only their respective new
gateway during the SQLite-state backup. First archive/restore is not accepted yet.
The standalone manifests avoid the incompatible legacy `backup` phase inherited
from the old Hermes base. Port-zero gateways are verified with actual Docker
health, gateway connections and tool/inference calls; `deploy apply --skip-verify`
avoids its unrelated HTTP-to-localhost probe. Async deploy replies are not proof.

Both private Mattermost connections and native DeepSeek Flash tool calls passed.
JARVIS is restricted to Kodeme ERP/Desk; all writes initially need Tri's native
approval. SENTINEL has scoped Dokploy/Mattermost diagnostics, Gatus history for the 68
Kodeme checks, and a separate native GlitchTip connection for the enrolled ERP/Desk
projects. Recovery and raw-log execution remain outside these read grants.
The linked setup and monitoring runbooks describe the current catalog and
acceptance limits.
Full permission and acceptance details:
[kodemeio-hermes setup](../../../kodemeio-hermes/docs/kodeme-agent-setup.md).
No IDTPP, existing Odoo/Mattermost deployment, legacy worker, VISION or TERA
service was restarted or reconfigured by this new-agent rollout.
