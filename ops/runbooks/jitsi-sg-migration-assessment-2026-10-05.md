# Jitsi migration to Singapore: feasibility assessment

**Status update:** The migration was subsequently executed on 2026-10-05.
Production now runs on Singapore `tpp-prod-06`. See the
[execution report](jitsi-sg-migration-execution-2026-10-05.md) for current
placement, validation, latency measurements, and rollback. The assessment below
records the state before migration.

Assessed 2026-10-05, approximately 11:49–12:00 WIB. Scope: read-only live
inspection and a proposed migration; no deployment, DNS, firewall, credentials,
or server sizing changed.

## Recommendation

Moving the complete Jitsi stack to `tpp-prod-06` is feasible for the currently
observed light workload. Use a staged deployment and representative meeting tests
before cutover. Keeping CPX32 is reasonable for a pilot and small meetings;
CPX42 or a dedicated Singapore meeting server is preferable if concurrent video
meetings and Mattermost calls become regular. There is no validated participant
capacity yet. Indonesian users should benefit from moving media nearer them,
but client latency and call quality have not been measured in this assessment.

## Live placement and capacity

| Item | Current Jitsi | Singapore destination |
|---|---|---|
| Server | `tpp-prod-01`, Nuremberg, Germany | `tpp-prod-06`, Singapore |
| Public IPv4 | `178.104.127.104` | `5.223.69.142` |
| Server type | CPX52, 12 shared vCPU / 24 GB | CPX32, 4 shared vCPU / 8 GB |
| Measured usable RAM | 23,456 MiB | 7,745 MiB |
| Measured available RAM | 3,792 MiB | approximately 6,103 MiB |
| Root filesystem | Not needed for destination sizing | 150 GiB total, 29 GiB used, 115 GiB available |
| Existing workloads | Jitsi plus shared infrastructure | Two healthy Mattermost apps, two Postgres DBs, two backup containers, maintenance page, Traefik |
| Swap | 8 GiB, almost entirely used | 8 GiB, approximately 29 MiB used; no swapping during short `vmstat` observation |

Dokploy confirms Jitsi compose ID `mWq0GSmAbyUtIjcEnWgCj` is on source server
ID `v_tUJaC4dlskl8fmie58K`. Destination Dokploy server ID is
`2-3nUPC6FJqt7QWabzFp1`; Hetzner server ID is `141354663`.

Current Jitsi has five healthy containers: web, Prosody, Jicofo, Videobridge,
and coturn. It runs `jitsi/*:stable-9823` and `coturn/coturn:4.6.2`.
Their combined sampled memory usage is approximately **257 MiB at idle**.
The seven named volumes occupy approximately **316 KiB** of allocated disk
blocks. Summing displayed image sizes gives approximately **2.12 GB**, before
accounting for shared layers. Disk capacity is ample; allow several GB for images,
logs, exports and backups. No Jibri recording container is running.

The live compose is **raw**, with auto-deploy disabled, despite its repository
metadata pointing to `kodemeio-jitsi/main`. Export the actual live compose and
environment securely when preparing migration; the manifest alone does not
reproduce the deployed definition. There are no backup records in the compose
detail returned by Dokploy; make and verify an explicit migration backup.

## Recent Singapore resource history

Read `sysstat` files for September 29 through October 5. October 5 is partial,
through 04:50 UTC. The first six days each contain 143 samples; October 5 has 29.

| UTC date | Mean CPU busy | Highest 10-minute CPU busy | Lowest available RAM | Highest 5-minute load | Highest 10-minute eth0 outbound rate |
|---|---:|---:|---:|---:|---:|
| Sep 29 | 2.34% | 5.35% | 5.86 GiB | 0.41 | 2.48 Mbps |
| Sep 30 | 2.38% | 5.97% | 5.94 GiB | 0.52 | 3.22 Mbps |
| Oct 1 | 2.44% | 5.88% | 5.79 GiB | 0.65 | 8.98 Mbps |
| Oct 2 | 2.34% | 6.02% | 5.89 GiB | 0.44 | 4.73 Mbps |
| Oct 3 | 1.78% | 4.31% | 6.04 GiB | 0.29 | 0.91 Mbps |
| Oct 4 | 1.77% | 4.22% | 6.06 GiB | 0.28 | 0.54 Mbps |
| Oct 5, partial | 3.04% | 5.81% | 5.93 GiB | 0.39 | 1.34 Mbps |

CPU steal was zero in these interval averages. These measurements establish
recent headroom, not capacity under new video traffic: short spikes are hidden
by averaging, and no representative Jitsi load test or client path test was run.
The virtual NIC reports speed `-1`, so its actual Internet throughput ceiling
was not established. Its interface counters show no errors or drops at inspection.

### Memory limits need attention

Jitsi's enforced container memory limits total **3.75 GiB**: web, Prosody and
Jicofo at 512 MiB each, JVB at 2 GiB, coturn at 256 MiB. Existing SG container
limits total **6.3125 GiB**, excluding unrestricted Traefik. Together that is
**10.0625 GiB**, above the host's approximately 7.56 GiB usable RAM.

Limits are ceilings, not preallocated consumption. Current usage fits easily,
but simultaneous growth can cause memory pressure, swapping or OOM kills.
Retain Jitsi's limits initially, monitor both meeting systems, and upgrade or
separate Jitsi before sustained concurrent workloads. Moving Jitsi alone removes
only approximately 257 MiB of observed idle usage from the EU host; it will not
resolve that host's broader swap pressure.

### Swap protection verified

Following the request to ensure swap on SG, verified `/swapfile` is active at
approximately **8 GiB**, with approximately **29.5 MiB** used. `/etc/fstab`
contains `/swapfile none swap sw 0 0`, and `swapfile.swap` is loaded and active,
so swap activation is configured for boot. Current `vm.swappiness=10` favors
RAM while retaining the swap buffer. No host reconfiguration was necessary.

Docker uses cgroup v2. All existing memory-limited containers have
`MemorySwap=2 × Memory`, allowing swap equal to their RAM limit; for example,
each Mattermost app permits 2 GiB RAM plus 2 GiB swap. Host swap is shared, so
these allowances do not reserve separate disk space for each container.
During SG Jitsi staging, inspect actual `MemorySwap` and cgroup
`memory.swap.max` for every new container and ensure swap is available rather
than setting combined RAM-plus-swap equal to RAM alone. Retain bounded memory
limits and normal OOM handling. Swap reduces OOM risk but cannot guarantee
avoidance, and sustained swapping can harm real-time media; see
[Docker's memory and swap documentation](https://docs.docker.com/engine/containers/resource_constraints/#--memory-swap-details).

## Networking and configuration

The shared Hetzner firewall `10805778` is applied to both servers. It already
allows TCP 80/443, UDP 10000 and TCP/UDP 5349. SG has no listeners on 10000 or
5349, and UFW is inactive. Mattermost uses TCP/UDP 8466 and 8467, which do not
conflict. SG has an external `dokploy-network` overlay network available.

At inspection, `jitsi.idtpp.com` resolves directly to the EU IPv4, has no AAAA
answer and serves HTTPS 200. The destination has no Hetzner private network
attachment. Moving the entire stack keeps its XMPP communication local; moving
only JVB would require a separately designed, secured inter-region connection.

Migration must change `JVB_ADVERTISE_IPS=178.104.127.104` to `5.223.69.142`.
Preserve the public hostname, realm, integration URLs and current authentication
settings. A DNS change alone does not relocate media. This requirement is
documented in the [Jitsi Docker guide](https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-docker/#running-behind-nat-or-on-a-lan-environment).

### TURN fallback is an unresolved validation gate

Live coturn arguments set relay range **49160–49200**, but the compose publishes
only TCP/UDP 5349, the cloud firewall has no relay-range rule, and the inspected
arguments contain no external-IP mapping. The web service also specifies
`TURN_TRANSPORT=udp`; publishing TCP 5349 alone does not demonstrate that browsers
can use TCP fallback on networks that block UDP.

These observations indicate an existing fallback risk; this assessment did not
prove an end-to-end TURN failure. Before cutover, validate browser ICE discovery
and force relay traffic from an external client. Confirm coturn's complete
effective configuration and then provide the required NAT mapping and relay
reachability, or use an alternative verified TURN arrangement. Confirm TCP/TLS
fallback if restricted-network support is required. Port 5349 does not by itself
prove TLS is configured. See the [coturn reference](https://github.com/coturn/coturn/blob/master/README.turnserver)
for `external-ip`, relay ports and TLS configuration.

Runtime `ENABLE_AUTH=0` and `ENABLE_GUESTS=1` also differ from the manifest's
description of Authentik JWT protection. Preserve existing behavior during
migration and verify the intended login/guest flow explicitly; do not assume the
manifest description proves that SSO is enforced.

## Costs and traffic

Live Hetzner API pricing for Singapore, net monthly server cap in EUR:

| Option | CPU / RAM | Monthly cap | Included outbound traffic |
|---|---|---:|---:|
| Existing CPX32 | 4 shared vCPU / 8 GB | €57.99 | 2 TiB |
| CPX42, currently available in SG | 8 shared vCPU / 16 GB | €109.99 | 3 TiB |

Keeping CPX32 adds **no server subscription cost**. CPX42 increases the listed
server cap by **€52/month**. These API prices exclude separately billed IPv4,
tax where applicable, backups and excess traffic. The API reports **€8.30 per
additional traffic TB** for these plans. SG's current outgoing traffic counter
is approximately 9.30 GB; the source plan includes 20 TiB, so relocating video
reduces the traffic allowance available to it substantially.

For scale only: sustained aggregate JVB egress of 50 Mbps is approximately
22.5 GB/hour; 100 Mbps is approximately 45 GB/hour. Meeting duration, received
streams, resolution and simultaneous sessions determine actual usage. Existing
SG services share the same allowance. [Hetzner bills outgoing traffic and
cross-region public-IP traffic](https://docs.hetzner.com/cloud/billing/faq/#how-do-you-bill-for-traffic).

## Proposed execution and acceptance gates

1. Export the live raw compose, environment and all seven volumes into a secure
   backup; verify archive integrity and restoreability. Reconcile the raw
   deployment with desired state before choosing the apply path. The current
   instance inherits the infrastructure base's backup block; review it rather
   than assuming a generic database backup fits Jitsi.
2. Create a separate SG candidate stack with unchanged image versions, unique
   volume names and SG media IP. Configure its Traefik route and HTTPS for a
   staging hostname. Resolve TURN configuration and test it. Keep EU production
   serving users throughout preparation.
3. Test from Indonesian clients on separate Internet connections with at least
   **three participants**, ensuring traffic actually uses the SG JVB. A two-user
   call may use peer-to-peer media and cannot validate the bridge. Exercise
   screen sharing, reconnects and forced TURN relay; run representative pilot
   sizes such as 5/10/20 participants alongside Mattermost calls. These are test
   sizes, not supported-capacity claims. Record CPU, available RAM, swap activity,
   OOM/restarts, bandwidth, packet loss, media RTT and user-visible quality.
4. Proceed only if all five containers remain healthy, media endpoints are on
   SG, TURN behavior meets requirements, and Mattermost calls remain stable.
   Suggested pilot operating margins: host CPU below 70% sustained, available
   RAM above 2 GiB, no active swap-in/out and no OOM kills. These are proposed
   operator thresholds, not Jitsi product guarantees.
5. Reserve a 15–30 minute maintenance window as a planning allowance. Drain
   existing EU meetings, take a final consistent copy of persistent volumes,
   restore the tested SG stack under the production hostname/realm and update
   the Dokploy route, manifest placement and DNS A record. Confirm certificate
   issuance and wait for the actual DNS TTL; client DNS caches can extend the
   transition. Existing meetings cannot be transferred transparently to a new
   bridge. Announce that clients may need to rejoin.
6. Keep EU containers and volumes intact for rollback. If acceptance fails,
   drain the SG candidate, preserve any new persistent state, restore the EU
   route and DNS, and let clients reconnect. Retain SG artifacts for investigation.
   Monitor both platforms through real business-hour traffic before retiring
   the old Jitsi stack. Never stop the EU Dokploy control plane or Traefik.

The [Jitsi requirements guide](https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-requirements/)
emphasizes network reliability and workload-dependent sizing. This assessment
supports a controlled move on current hardware, conditional on the media tests;
it does not certify large-meeting capacity or recording capacity.

## Evidence sources and limits

Live evidence: Dokploy `compose list/get`, repository `hosts` inspection,
Hetzner `servers get/list`, `firewalls get`, `server-types list`, SSH `docker ps`,
`docker stats`, selected `docker inspect` fields, volume sizes, `free`, `df`,
`ss`, `ufw`, `vmstat`, `sar/sadf`, interface counters, DNS and HTTPS probes.
Credentials and meeting identifiers were excluded from this report.

The local manifest was corroborated against live placement. No current Jitsi
participant peak could be obtained: local `/colibri/stats` probes returned 404.
Neither actual Singapore client latency, peak new-workload bandwidth nor TURN
functionality was measured. No changes to production were made.
