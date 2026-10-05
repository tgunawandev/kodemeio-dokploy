# Jitsi production migration to Singapore

Executed 2026-10-05. Production `https://jitsi.idtpp.com` now runs on
`tpp-prod-06` in Singapore (`5.223.69.142`). The EU Jitsi containers are stopped;
their Dokploy record, environment, and seven volumes remain available for
rollback. No server was deleted or resized.

## Production placement

| Item | Value |
|---|---|
| Production compose name | `tpp-infra-jitsi` |
| Production compose ID | `ecS_rlMmI25Pnc-AdedA3` |
| Production app / volume prefix | `compose-bypass-neural-panel-ks7gn8` |
| Dokploy SG server ID | `2-3nUPC6FJqt7QWabzFp1` |
| Hetzner SG server ID | `141354663` |
| Final production deployment | `mHazK2QF_JajEew3j4Dmr`, completed |
| EU rollback name | `tpp-infra-jitsi-eu-rollback` |
| EU rollback compose ID | `mWq0GSmAbyUtIjcEnWgCj` |
| EU app / volume prefix | `compose-parse-virtual-panel-2yblgs` |
| EU address / server | `178.104.127.104`, `tpp-prod-01` |
| Production DNS record ID | `2bbe800431708b9b27670ba6ad50ff88` |
| DNS | A → `5.223.69.142`, DNS only, TTL 60 seconds |
| Diagnostic alias | `jitsi-sg.idtpp.com` also routes to SG |

The production hostname is unchanged, so existing meeting links and integrations
continue to use the same URL. Authoritative DNS and resolvers `1.1.1.1` and
`8.8.8.8` returned SG; an ordinary HTTPS request connected to SG and returned
HTTP 200 with successful certificate validation. The EU stack was stopped after
the previous approximately 300-second DNS TTL had elapsed and its bridge
reported zero active conferences.

## Configuration and capacity

The migrated raw Compose definition preserves `jitsi/*:stable-9823`,
`coturn/coturn:4.6.2`, existing credentials, and existing authentication settings
(`ENABLE_AUTH=0`, `ENABLE_GUESTS=1`). JWT authentication was not enabled as part
of this move.

Singapore has 4 shared vCPU and 7,745 MiB usable RAM. After validation, available
RAM was 5,388 MiB. All five Jitsi containers were healthy, with zero OOM kills
and zero restarts since the production deployment. The two existing Mattermost
instances returned `status: OK`; their application/database containers remained
healthy. This includes new monitoring services present during the final check.

Existing **8 GiB host swap** was verified active and persistent through
`/etc/fstab` (`/swapfile none swap sw 0 0`), with `vm.swappiness=10` and only about
30 MiB used. The swap file is owned by root with mode 0600. Container swap
access is explicitly enabled rather than relying on Docker defaults:

| Service | RAM limit | Additional swap allowed | Compose `memswap_limit` |
|---|---:|---:|---:|
| Videobridge | 2 GiB | 2 GiB | 4 GiB |
| Web | 512 MiB | 512 MiB | 1 GiB |
| Prosody | 512 MiB | 512 MiB | 1 GiB |
| Jicofo | 512 MiB | 512 MiB | 1 GiB |
| coturn | 256 MiB | 256 MiB | 512 MiB |

Docker inspection confirmed each combined RAM+swap limit. JVB's cgroup v2 swap
limit was also checked. Swap provides reserve during memory pressure; host or
container limits can still cause OOM. These short tests do not establish maximum
meeting capacity or sustained concurrency with Mattermost calls.

The destination advertises `5.223.69.142` for JVB media and coturn relay
allocations. TURN discovery is now configured in Prosody, which advertises TCP
and UDP TURN on production port 5349. coturn shares the internal bridge with
JVB and publishes UDP relay ports 49160–49200. Existing firewall rules cover
UDP 10000 and TCP/UDP 5349. Added firewall `tpp-prod06-jitsi-relay` (ID
`11735199`) permits UDP 49160–49200 and is applied only to SG server `141354663`.
The shared firewall and Mattermost ports 8466/8467 were preserved.

## Latency and meeting validation

Measurements came from the operator workstation in Indonesia on one client
network path. EU and SG HTTPS requests used the same production hostname with
explicit origin IP selection. Values are sample averages, not a service SLA.

| Measurement | EU | Singapore | Reduction |
|---|---:|---:|---:|
| ICMP RTT, 8 packets per host | 277.93 ms | 21.34 ms | 92.32% |
| HTTPS time to first byte, 5 fresh requests per host | 870.39 ms | 300.54 ms | 65.47% |
| WebRTC media RTT, 3 participant snapshots | 353.33 ms | 30.67 ms | 91.32% |

Both ICMP samples had zero packet loss. All ten HTTPS latency samples returned
HTTP 200. WebRTC values were sampled selected-candidate RTTs, not one-way audio
delay; browser startup and transient scheduling affect them.

Validation used isolated synthetic rooms, Chrome, Playwright, and fake camera /
microphone streams. P2P was disabled to exercise Videobridge media. No test
messages were posted to Mattermost channels.

- Staging 3- and 5-participant calls joined and received audio/video through SG
  UDP port 10000. During the 5-participant test, sampled bridge stress was
  approximately 0.019 and JVB RAM usage approximately 296 MiB.
- Production 3-participant SG call passed: all participants received media,
  selected public bridge `5.223.69.142:10000`, and decoded video frames.
- Equivalent EU 3-participant call passed before stopping EU and supplied the
  comparison above.
- Standalone forced TURN UDP and TCP data channels both passed, echoing ten
  payloads per transport through SG relay allocations.
- Production forced TURN TCP Jitsi call passed with all three participants
  connected and receiving media. Browser ICE policy was `relay`; public relay
  candidates were `5.223.69.142`, with ports 49173, 49165, and 49166. Selected
  candidates appeared as peer-reflexive `172.19.0.2` on those same allocation
  ports, with `relayProtocol=tcp`, because coturn and JVB share the Docker
  bridge. Initial strict candidate-type assertions incorrectly rejected this
  working path; the final check verified policy, allocations, transport, and
  received media. This verifies plain TCP TURN, not TURN over TLS.

## Mattermost integration

Live runtime configuration on `https://mm.idtpp.com` confirms Jitsi plugin
`jitsi`, version `2.1.0`, is active. Its configuration is:

```text
jitsiurl = https://jitsi.idtpp.com
jitsiembedded = true
```

The plugin's same-origin SDK endpoint
`https://mm.idtpp.com/plugins/jitsi/jitsi_meet_external_api.js` returned HTTP 200
as JavaScript. The embedded-call check uses this actual endpoint from a page
served at the Mattermost origin. **The three-participant embedded call passed:**
all three browsers received media through `5.223.69.142` and decoded video
frames (137, 127, and 11 frames at the acceptance snapshots). This checks the
plugin SDK and cross-origin media path; it does not automate the authenticated
Mattermost channel button or post a meeting announcement. Mattermost's native
Calls plugin is a separate service; its voice-call UI was not exercised by the
Jitsi migration tests.

## Audio follow-up and live resource check

After an audio issue was reported, checked production audio with a controlled
440 Hz microphone source, received Opus audio energy, and HTML audio playback
state. The browser autoplay bypass flag used in earlier migration tests was
omitted for these checks. All of the following passed:

- Direct two-participant Jitsi audio through the SG bridge.
- Two-participant Mattermost embedded audio using P2P.
- Mattermost embedded audio forced through Singapore TURN.
- Mattermost embedded audio received by a participant with microphone and
  camera muted.

Both receiving browsers in the bidirectional tests decoded non-silent audio;
their audio elements were playing, unmuted, at full volume. The user subsequently
confirmed audio was working. No production configuration was changed during
these diagnostics. The precise cause of the transient user-reported failure
was not established; the checks did not inspect physical speaker output or the
affected devices remotely.

Checked SG resources again at **2026-10-05 13:08 WIB (06:08 UTC)** during one
live **four-participant** meeting:

| Resource | Observed |
|---|---|
| CPU | 5.94% busy over five seconds; subsequent three-second average 7.14%, across four vCPU |
| Load average, 1 / 5 / 15 minutes | 0.11 / 0.23 / 0.24 |
| Available RAM | 5,405.7 MiB of 7,745.7 MiB (5.28 GiB of 7.56 GiB) |
| Jitsi containers, total RAM | 481 MiB |
| Videobridge RAM | 289.6 MiB of 2 GiB limit |
| Host swap | 30.3 MiB used of 8 GiB, persistent; zero sampled swap-in/out |
| Root disk | 31 GiB used, 114 GiB available, 22% used |
| Memory / I/O pressure | Zero in the 10-second PSI averages |
| Container health | Jitsi and both Mattermost apps/databases healthy, zero OOM kills or restarts |
| Active bridge | Three audio senders, two video senders; aggregate RTT 27.22 ms |

Both Mattermost HTTP health checks returned `status: OK`. The current meeting
has ample observed headroom; this short sample does not establish maximum
participant capacity. Detailed audio and resource samples are included in the
sanitized migration evidence JSON linked below.

## Backups and certificate renewal

Exported the source raw Compose and environment privately, and archived all
seven named volumes. For the final archive, all 54 source files had unchanged
SHA-256 hashes before and after the archive operation. Source volumes were
restored into SG volumes while the candidate containers were stopped.

Final archive: `volumes-final.tar.gz`

```text
SHA256: 708931c0a1c3e1b3a12c1b4d8f8dfe4f5c6c685e3f9186e31083baac843b1277
```

Archives are retained on both hosts at
`/var/backups/jitsi-migration-20261005/` and locally at
`/home/tgunawan/.local/state/kodemeio/jitsi-migration-20261005/`.
Local exports and credentials remain outside version control. No scheduled
volume backup existed on the source; these are migration snapshots.

Imported only the production certificate temporarily to validate the SG origin
before DNS cutover. Once DNS pointed to SG, removed that temporary dynamic
certificate configuration and verified a newly issued production certificate
in SG Traefik's ACME store. It is valid through 2027-01-03 and uses automatic
renewal. The temporary import is archived, outside the watched Traefik dynamic
directory. Traefik and the Dokploy control plane were never stopped.

## Subsequent deployment

Desired placement is recorded in
`deploys/instances/production/tpp-infra-jitsi.yaml`; the live raw Compose is
stored in `deploys/composes/jitsi/docker-compose.prod.yml`. The ignored local
production env file was updated with SG settings. Manifest validation passed.

**Use the repository front door.** The current generic `deploy apply` assumes
a GitHub source even for a manifest specifying `source.type: raw`; use the
following procedure for this stack, previewing mutations with `--dry-run` first:

```bash
./dokploy.sh idtpp compose update ecS_rlMmI25Pnc-AdedA3 \
  --source-type raw --compose-file deploys/composes/jitsi/docker-compose.prod.yml --yes
./dokploy.sh idtpp env push ecS_rlMmI25Pnc-AdedA3 \
  deploys/env/production/.env.tpp-infra-jitsi --yes
./dokploy.sh idtpp compose start ecS_rlMmI25Pnc-AdedA3 --yes
./dokploy.sh idtpp deployments list --compose ecS_rlMmI25Pnc-AdedA3 --limit 1
```

Poll deployment completion and all five container health checks; queuing a
deployment alone does not establish success. Auto-deploy remains disabled.

## Rollback

Rollback is prepared but was not executed. Production calls must be drained or
treated as interrupted when changing the bridge; active conferences do not
transfer between hosts.

1. Start the retained EU stack and verify HTTPS and bridge health against its IP:

   ```bash
   ./dokploy.sh idtpp compose start mWq0GSmAbyUtIjcEnWgCj --yes --dry-run
   ./dokploy.sh idtpp compose start mWq0GSmAbyUtIjcEnWgCj --yes
   ./dokploy.sh idtpp deployments list --compose mWq0GSmAbyUtIjcEnWgCj --limit 1
   curl --resolve jitsi.idtpp.com:443:178.104.127.104 https://jitsi.idtpp.com/
   ```

2. Restore the production A record to EU after that check passes:

   ```bash
   kctl-cf -p idtpp records update 2bbe800431708b9b27670ba6ad50ff88 \
     --zone idtpp.com --content 178.104.127.104 --ttl 60 --no-proxied
   ```

3. Verify DNS propagation and an EU meeting, then stop only the SG Jitsi compose
   after conferences drain:

   ```bash
   ./dokploy.sh idtpp compose stop ecS_rlMmI25Pnc-AdedA3 --yes --dry-run
   ./dokploy.sh idtpp compose stop ecS_rlMmI25Pnc-AdedA3 --yes
   ```

Original EU settings still advertise EU media and TURN addresses. If SG volume
data changes materially after migration, assess and archive that state before
rollback instead of overwriting the retained EU snapshot blindly. Reconcile
compose names and the desired manifest if rollback becomes the permanent state.

Sanitized measurements and acceptance results are in
[`ops/evidence/jitsi-sg-migration-2026-10-05.json`](../evidence/jitsi-sg-migration-2026-10-05.json).
