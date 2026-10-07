# kod-ovh-01 — OVHcloud VPS for kod/Teracorp capacity

Status: **active**, provisioned 2026-10-05. Registered in Dokploy as `kod-ovh-01`
(server id `W-sYqWjxLu9cAhp41pwpA`). Purpose: kod/Teracorp production capacity
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
  `OVH_APPLICATION_KEY` / `OVH_APPLICATION_SECRET` / `OVH_CONSUMER_KEY` + the
  current per-install console password. TODO: move to a 1Password `OVH` item in
  the Kodemeio vault (the kctl-op CLI cannot create items yet — needs the
  desktop-app integration).
- OVH API requests sign `$1$` + SHA1(`AS+CK+METHOD+URL+BODY+TIMESTAMP`) and send
  `X-Ovh-Application` / `X-Ovh-Consumer` / `X-Ovh-Signature` / `X-Ovh-Timestamp`.

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

## Deferred / next

- No workloads yet. When a service lands here: add its ports to ufw (reference:
  Hetzner `firewall-1` rules) and decide Alloy/Gatus coverage.
- Backups for host data (beyond OVH automated backup) are decided with the
  first stateful workload.
- `deploys/instances/production/kod-infra-omp.yaml` still names `server:
  kod-prod-02`; no `kod-prod-02` entry exists in Dokploy as of 2026-10-05
  (servers changed outside this session — recreate as `kod-hzc-02` when
  intended).


## Kodeme operator agents — 2026-10-07

Tri authorized new JARVIS and SENTINEL setup on this host. The independently
verified deployments are `kod-infra-hermes-jarvis-tera`
(`4nr-Gy9QaWV0ApEcVrp3H`) and `kod-infra-hermes-sentinel-tera`
(`iKyt78Oq8Gmp29yeOkhIN`), under `apps` / `production`. Their matching
[manifests](../../deploys/instances/production/kod-infra-hermes-jarvis-tera.yaml)
and [SENTINEL manifest](../../deploys/instances/production/kod-infra-hermes-sentinel-tera.yaml)
use `kodemeio-hermes:v2026.8.31`, pinned upstream commit
`29112bef099274229cadff79cdff7bf7b99c4b77`, verified local build context
`/opt/hermes-upstream/v2026.8.31`, and source branch
`agent/kodeme-jarvis-sentinel` of `tgunawandev/kodemeio-hermes`.
Auto-deploy is disabled. Never enable it by relying on the manifest alone:
this installed deploy orchestrator initially left the new compose API flags true;
explicit `compose update --no-auto-deploy` and re-reading the API corrected them.

Each gateway is capped at 1 CPU / 1 GiB and has a separate state volume. SENTINEL
alone adds `kod-operations-read-sentinel` (0.5 CPU / 256 MiB), image
`kodemeio-operations-read:bb8418c8ce48`, source
`/opt/kodemeio/operations-read/bb8418c8ce48`. Its private policy/provider/bearer
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
approval. SENTINEL's read backend permits only Dokploy stacks/servers, Mattermost
health and Gatus deployment metadata. Recovery, raw logs, GlitchTip events, Gatus
check history and proactive alert scheduling are not provisioned.
Full permission and acceptance details:
[kodemeio-hermes setup](../../../kodemeio-hermes/docs/kodeme-agent-setup.md).
No IDTPP, existing Odoo/Mattermost deployment, legacy worker, VISION or TERA
service was restarted or reconfigured by this new-agent rollout.
