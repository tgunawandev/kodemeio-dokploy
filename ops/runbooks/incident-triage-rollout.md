# VERONICA incident triage rollout

## Verified starting state (2026-10-05)

`KCTL_DOKPLOY_PROFILE=kodemeio ./dokploy.sh services kodemeio --json` returned
16 services with **no Gatus or GlitchTip registration**. This contradicts the
2026-09-27 monitoring README's claim that Gatus runs. It does not prove an
independently hosted monitor is absent: inventory that host before creating one.
The configured GlitchTip URL's real organization API read timed out. The
`kctl-glitchtip doctor` currently checks configured URL/token only and is not a
connectivity probe. Do not use its green result as deployment evidence.

Current authoritative Gatus config remains `ops/monitoring/gatus/config.yaml`
and its pinned 5.37.0 Compose. Do not deploy the retired `kodemeio-gatus` copy.
The old GlitchTip desired state points at service `web`, while the support repo
defines `glitchtip`, defaults to old 4.1, and disagrees about env file/port.
The local pilot instead uses upstream 6.2.6 with its verified Docker Hub tag
**without a `v` prefix**, pinned by digest, all-in-one worker, PostgreSQL 16,
and backend port 8000. This is not an upgrade of a production database.

## Installed local pilot

From this repository:

```bash
python3 ops/monitoring/incidents/pilot.py install
python3 ops/monitoring/incidents/pilot.py test
python3 ops/monitoring/incidents/pilot.py status
```

Ports: Gatus dashboard `127.0.0.1:8786`, controlled synthetic target
`127.0.0.1:8787`, incident receiver
`127.0.0.1:8788`, GlitchTip `127.0.0.1:8789`. Secrets are generated once into
ignored mode-0600 `.env.local`. Persistent volumes belong only to
`kod-local-incident-pilot`; no shared production network/database is used.
`stop` preserves them. Do not use `down -v` unless deliberately retiring the
pilot and its evidence. The seeded API identity has no interactive password and
only org/project/event read scopes. A separate local UI operator can log in as
`operator-local@example.invalid`; `pilot.py login-details` gives the private
password file and key name without printing the password. Browser login and
organization access have been verified. No human alert recipient is configured.

The test forces three failed Gatus checks then two successes, checks recovery
invalidates old evidence, sends a clearly synthetic exception into real
GlitchTip, waits for its native alert webhook and readable stack trace, and
exports ignored `ops/monitoring/incidents/evidence/synthetic-incident.json` plus
`acceptance.json`. No application defect or production outage is manufactured.

The local evidence collector polls unresolved GlitchTip issues every 15 minutes
and drains the outbox every five seconds, with an IDs/statuses-only daily digest
in its local output. This is proactive **evidence collection**;
it does not start an OMP model session. Selected warning/error/fatal log breadcrumbs
are included, while user/request/context/stack-variable data are dropped.
Pattern redaction is best-effort: inspect the bundle before model use.

Validation commands for this slice:
`uv run pytest ops/monitoring/incidents/tests -q`,
`uv run ruff check ops/monitoring/incidents`, and the worker's own unit/format
suite. The configuration tests currently require the sibling Hatchet checkout;
the Hatchet package has an independent CI job. Run the Docker pilot separately
to verify real delivery. No remote CI run is claimed for uncommitted changes.

## Model-backed pilot

Use independent, clean code-only checkouts. Export/inspect the bundle and run:

```bash
omp incident ops/monitoring/incidents/evidence/synthetic-incident.json \
  --cwd /path/to/clean/kodemeio-next --dry-run

omp incident ops/monitoring/incidents/evidence/synthetic-incident.json \
  --cwd /path/to/clean/kodemeio-next --preset deepseek --batch
```

Both personas support identical tools and YOLO defaults; `--persona friday`
changes the persona, and VERONICA is the default. Reports stay in private persona
state outside source. DeepSeek is an explicit paid selection; default Codex
requires OMP's own login. No fallback is attempted. Check synthetic labeling,
severity, facts versus hypotheses, inspected head versus event release,
reproduction/check evidence and a scoped FRIDAY handoff. An incident report must
never be treated as formal exact-head code-review approval.

## Cloud evidence service and monitoring activation

1. Verify any independently hosted Gatus, its heartbeat, routing and alert
   delivery. Record exact host/service ids. Preserve Telegram/email and the
   external Healthchecks dead-man alarm. Only add the custom incident recipient
   after a real authenticated receiver exists.
2. Decide the GlitchTip host and PostgreSQL database owner; verify backups and
   restore before using live application data. Reconcile the old manifest's
   source/service/env/version/port with the tested deployment. Do not guess a
   tenant database, silently upgrade an existing database or duplicate a monitor.
3. Build/publish an immutable reviewed collector image from
   `kodemeio-hatchet/workers/incident_triage/deploy/Dockerfile` and supply its
   reference to that package's `deploy/docker-compose.yml`. Both local/cloud
   use that Dockerfile and lockfile. The cloud ingress and Hatchet worker share
   one persistent SQLite volume on one host; do not distribute it over NFS.
4. Populate `services.json` from `services.example.json`: explicit service,
   repository, environment, endpoint names, GlitchTip organization/project.
   Use dedicated projects per environment and a project-limited read identity.
   Configure scoped ingest/read/Hatchet credentials outside code and agent state.
   The ingress receives no GlitchTip read token or Hatchet token; the worker
   receives neither provider nor GitHub/deployment credentials.
5. Configure TLS ingress and **suppress reverse-proxy access logging** on the
   URL-token GlitchTip webhook route. Its native recipient supports URL-based
   ingestion auth; do not leak that URL through notifications or prompts.
6. Test the live Hatchet SDK adapter with synthetic incident ids: one slot,
   replay, duplicate submission/lost reply, old generation, API 401/429/5xx,
   project/environment mismatch, deadline, recovery and state restore. Hatchet
   run input/output must contain ids/statuses only. Configure pending-age/failure
   monitoring, retention, backups and a daily unresolved-incident digest before
   enabling the receiver for real services. The digest must contain ids and
   links to operator-controlled reports, not raw logs or exception text.
7. Preview and apply through `./dokploy.sh` with explicit platform; wait for
   asynchronous completion and verify real API reads, health, alert delivery and
   the external dead-man check. Store sanitized acceptance evidence, never
   tokens, raw logs or OAuth material.

For Next.js apps, the shared Sentry configuration supports
`NEXT_PUBLIC_SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_ENVIRONMENT` and
`NEXT_PUBLIC_SENTRY_RELEASE` (set release to `<app>@<git-sha>` in CI). Public
variables must be present **at build time** for browser bundles. Keep the
GlitchTip management/source-map-upload token only in the CI/operator job. Verify
browser, server and edge events plus readable source maps independently. Errors
and log support must be verified against the app's pinned SDK; installing a
server does not instrument every app. Extend to Odoo/Hatchet workers only after
the first application has accepted evidence.

## Continuous agent dispatch remains a separate gate

The evidence worker has no agent invocation. Before enabling automatic VERONICA
sessions, satisfy OMP `docs/migration.md` section 4: contained authenticated
runner, clean independent clones, idempotency, process-group cleanup, deadlines,
provider hard spend stops, trusted outcome/report extraction and a real canary.
Add alerts → VERONICA triage only after that acceptance. A FRIDAY fix is a
separately scoped work order; VERONICA then reviews its exact frozen commit.
The operator remains the sole publisher/deployer. OMP's new command must be
promoted to the cloud image through its own release process; local installation
does not change the currently deployed cloud terminals.

If retiring anything, retain its image/config/data and exact cutover evidence;
inventory consumers first. No live legacy runner or monitoring service was
stopped by this pilot.

## Desk and staging acceptance

Use the established https://desk.idtpp.com, company 1 and existing team 3 (IT
Development) for new monitoring engineering tickets. Preserve the original
ticket/team for human reports. Rollout ticket
[HT00530](https://desk.idtpp.com/web#id=530&model=helpdesk.ticket&view_type=form)
holds the local acceptance status and VERONICA's reviewed synthetic triage.
The internal-note bridge was verified live: replay returns the same note id.
There is no new ticketing engine and no automatic ticket closure.

See OMP `docs/incidents.md` for selected ticket creation, original-human-ticket
export and internal-note publication commands. Use a stable external key per
monitoring incident; unrelated bugs must not all be assigned rollout ticket 530.
Link that ticket in the selected bundle before OMP investigates real staging or
production evidence. The collector and OMP hold no Desk admin credentials.

Bug flow: existing/new Desk ticket → VERONICA triage → FRIDAY fix in staging →
staging checks → VERONICA independent exact-commit review → human acceptance →
operator production deployment. Record each report/action on the same ticket.
Local synthetic checks are permitted; YOLO never grants production authority.

## Prepared cloud candidate, not deployed

`ops/monitoring/incidents/compose.cloud.yml` combines the tested evidence loop,
Gatus 5.37.0, GlitchTip 6.2.6 and PostgreSQL 16.10 with the external dead-man
sidecar. It has no host ports, provider/GitHub/Desk credentials or agent runner.
The collector uses the exact local Dockerfile/lockfile, promoted by immutable
image digest. GlitchTip file storage is a persistent volume, initialized for the pinned
image's verified uid 5000 by a networkless one-shot container. Both first and
repeated local installation have passed. The separate Hatchet adapter remains optional until live acceptance;
it is not a prerequisite for starting evidence collection.

Before a production apply, attach this concrete plan to HT00530 for human review:

1. Select the independent monitoring host and record its Dokploy server id.
   The two reachable production hosts had about 1.1/1.6 GiB available. Candidate
   steady-state container memory limits sum to 1,824 MiB, before host/Traefik overhead
   and the transient 64 MiB file-volume initializer; those
   observations do not establish sufficient spare capacity. No paid host has
   been provisioned. Do not use the stale Gatus manifest's `kod-prod-01` as proof
   of an independent host: that machine currently runs production applications.
2. Build a reviewed immutable collector image. Create `services.cloud.json`
   from the Hatchet package's `deploy/services.example.json`, initially for one
   accepted staging app/project. Every Gatus endpoint receiving the custom alert
   must have an explicit service/repository/environment mapping.
3. Generate `gatus.cloud.json` from the authoritative Gatus config, preserving
   Telegram/email and the external heartbeat. Add the local pilot's authenticated
   custom recipient only for mapped endpoints; point it to the internal receiver.
   Complete the existing alert credentials and heartbeat URL outside Git.
4. Create a dedicated fresh GlitchTip database; this plan does not upgrade a
   legacy database. Seed the organization, staging project and scoped read-only
   identity through an operator session. Supply generated secrets, approved SMTP
   configuration, an HTTPS `GLITCHTIP_DOMAIN`, and `ALLOWED_HOSTS` including that
   hostname and `localhost`. Route GlitchTip to service `glitchtip`, port **8000**;
   the old service `web`/port 8080 desired state is incompatible with this image.
5. Configure an approved HTTPS receiver route to service `incident-ingress`, port
   8080, suppressing access logs for URL-token webhooks. Cloud private-IP webhook
   targets are disabled; GlitchTip's recipient must use that approved HTTPS URL.
   Gatus can use its internal authenticated receiver URL. Verify authentication,
   actual org/project reads and all liveness checks before enabling real alerts.
6. Verify PostgreSQL and file-storage backup/restore, the evidence online backup,
   queue depth/pending age/quarantine alerts and retention. Accept one real staging
   app's browser/server/edge events, release identity and uploaded source maps.
   The local disposable SDK fixture proves delivery but does not prove a deployed
   application or source-map upload.
7. Record exact source revisions/image digests, service/host ids, routing and
   staging acceptance on HT00530. Obtain explicit human production acceptance,
   then preview/apply through `./dokploy.sh` and verify asynchronous completion.
   Leave legacy consumers and services intact until their own cutover is proven.

Automatic VERONICA dispatch and unattended ticket publication need a separate
scoped Desk service identity, contained runner and proven spending stops. Current
report publication is operator-driven and has been exercised against the real Desk.
