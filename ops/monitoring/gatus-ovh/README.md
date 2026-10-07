# Kodeme OVH Gatus

Deployment `kod-infra-gatus-ovh` (`TArUcMCkn5KFSlqrzx-ij`) runs on `kod-ovh-01`
at https://gatus.kodeme.io. Native Basic authentication protects the status API;
`/health` and the dashboard shell are public. Native badge summaries are public.
The dashboard needs the protected API to load detailed histories. Credentials
and application/customer payloads are excluded from the checked-in definitions.

SQLite persists ten result/event samples per endpoint. There are 14 HTTPS checks
and 54 external container checks: 36 OVH, 14 HZ and 4 ORCA-host components. The
collector/catalog.json file is the authoritative host-component enrollment list.
The WeKnora machine MCP route `/mcp/` must return its backend-specific JSON
authentication refusal; an ingress-generated 404 cannot pass. Its workers also
have collector health checks. Dokploy and the legacy Hatchet admin probes do not
follow redirects and require a closed gate (302/401/403), matching the existing
independent monitoring contract. Gate refusal does not prove backend health;
the associated container checks cover runtime state.
HTTP checks include ERP, Desk, HRIS, Mattermost, Authentik, Dokploy, GlitchTip,
Gatus, WeKnora routes, Desk links, portfolio, Immich and the existing legacy
Hatchet service. Monitoring an existing legacy component does not restore it
to the target architecture or create an agent identity.

Host timers run the reviewed collector every minute. It reads only Docker
name, state, health state and Swarm service identity, never logs, environment,
container commands or business data. Native Gatus external endpoints enforce
per-host bearer push credentials and mark a missing heartbeat stale after three
minutes. Push secrets exist only on their enrolled host and in Gatus's private
environment; agents cannot obtain them. Gatus has no Docker socket mount.

Completed init containers and the superseded, intentionally stopped OVH ORCA
container are excluded. ORCA's active host is 46.225.42.130. No live Mailcow,
Chatwoot, WAHA, Nextcloud, Jitsi or RMM deployment was found in this three-host
inventory; architecture entries are not treated as deployed services. The
portfolio's domain is monitored, but its Dokploy registration had error status
and no local container. Runtime state checks prove liveness, not backup freshness
or recovery. The existing Mattermost database backup was restarting at enrollment.

No duplicate notifications were configured. The independent HZ Gatus compose
`RLyvhEXQhvPFrTgb2ZQ1V` and existing alerts remain unchanged. Its runtime and
heartbeat are themselves monitored. Independent HZ alerting is needed for OVH
failures because SENTINEL and this Gatus share the OVH failure domain.

SENTINEL's private operations_read MCP adapter exposes gatus.status at a fixed
origin/profile, with 50 rows per page, ten samples per endpoint and a 256000-byte
source response limit. Only endpoint identity, timestamp, success and duration
are returned. Gatus has no native MCP server. JARVIS receives no monitoring
credential or backend bearer. The role policy is owned by kodemeio-hermes.

Source branch feat/kodeme-ovh-monitoring is published; auto-deploy is disabled.
Native Dokploy redeploy is explicit. Private ignored env:
`deploys/env/production/.env.kod-infra-gatus-ovh`. Collector systemd units live
in collector/ and use immutable source bundles on each host, with mode-0600
push credentials in /etc/kodemeio/gatus-collector/. Updates require a deliberate
collector bundle/config rollout and native Gatus redeploy. Missing, ambiguous,
unhealthy or restarting containers report failure. Do not suppress observed
failures to make the monitor green. Database/volume backup verification is separate.

PR #15 review corrections are not deployed. Retired Swarm task containers are
ignored when selecting the active service; named stopped containers still fail,
and multiple active replicas remain ambiguous. An unauthenticated live check on
2026-10-07 found both admin origins returning 200. The stronger gate probes will
therefore correctly alarm until their intended gates are configured; the current
deployed 68-check snapshot predates these corrections. No gates or production
services were changed during PR review.
