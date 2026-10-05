# VERONICA monitoring rollout status — 2026-10-05

## Required workflow

The existing **https://desk.idtpp.com** is the central ticket record for
VERONICA. Keep a human bug report on its original ticket; link monitoring
incidents to that ticket where related instead of creating a second report.
Record investigation, evidence limitations, FRIDAY work orders, staging fixes,
tests, exact-commit independent review and the human's production acceptance
on that ticket. Gatus recovery alone must not close a software bug ticket.

All fixes and verification target **staging**. Isolated local synthetic tests
are permitted. Production deployment needs explicit acceptance by the founder
or another human after review. YOLO does not confer deployment authority.
This rollout does not deploy or repair production applications automatically.

## IDTPP production activation

The later explicit request to activate `gatus.idtpp.com` and `glitchtip.idtpp.com`
authorizes production monitoring. Gatus is now running on tpp-prod-06; existing
healthy GlitchTip v4.1 on tpp-prod-01 was reused without a database upgrade.
Five real health checks and the production native GlitchTip synthetic pipeline
passed; the protected collector queue drained. Eight existing Odoo projects
plus the synthetic project have native evidence-intake alert recipients.
Runtime details, parity limits, private login-file location and reproduction:
[IDTPP production monitoring](../monitoring/idtpp/README.md).

This supersedes the IDTPP cloud-pending status below, not the separate Kodemeio
estate inventory. Application fixes retain staging/review/human production gates.
The worker now has 21 passing tests, including v4 legacy API scope enforcement.

## Earlier local verification

- Local Gatus 5.37.0 is running: http://127.0.0.1:8786.
- Local GlitchTip 6.2.6 is running: http://127.0.0.1:8789.
- The receiver and evidence collector are running with a dedicated database.
- Local GlitchTip browser login and organization access passed. Use
  `python3 ops/monitoring/incidents/pilot.py login-details` to locate its private
  password, not a production login.
- Fresh Gatus failure/recovery and native GlitchTip alert delivery passed after
  the worker changes. These are synthetic tests, not production app telemetry.
- The worker's 19 tests pass, including leases, restart, backoff/quarantine,
  pagination, protected operations, retention and backup/restore.
- Desk's real API is connected to Odoo 18, database `tpp_odoo_helpdesk`.
  The observed 479 active tickets and existing Review stage establish that this
  is the established service desk, not a new ticket system.
- Verified company: 1, PT TUNGGAL PODO PAKERTI. Existing teams: 1, IT Operations
  & Support; 2, IT Analyst; 3, IT Development. New engineering incident tickets
  use team 3. Existing human ticket ownership is preserved.
- Rollout ticket: [HT00530](https://desk.idtpp.com/web#id=530&model=helpdesk.ticket&view_type=form).
  Live internal-note publication and identical-note replay passed. VERONICA's
  operator-reviewed synthetic triage is recorded there; no code fix was justified.
- Online backup and restoration of the running evidence database to a separate
  path passed SQLite integrity checks and preserved all eight incidents in that snapshot.
- GlitchTip PostgreSQL dump restored to a separate local database with all 13
  issues present at backup time. Persistent file storage survived container recreation.
- OMP: 36 tests, doctor and upstream settings validation pass. Incident/Desk
  configuration: 9 tests pass. Existing deployment suite: 1,288 pass, 2 skip.

The status is an observation, not a claim that every ticket or app was audited.

## Changes

`kodemeio-hatchet/workers/incident_triage` now has durable collection leases,
backoff and quarantine, bounded multi-page GlitchTip sweeps, protected operational
status, collector liveness, SQLite backup/restore to a new path, and retention
of completed recovered health incidents. Unresolved incidents remain retained.

`./dokploy.sh incident-inventory kodemeio --json` records actual containers
alongside Dokploy server records. Its optional `--diagnose` selects bounded,
pattern-redacted errors from VISION initialization and the DSH outpost only.
Never commit its diagnostic output as logs.

`ops/monitoring/incidents/desk.py` is an **operator** bridge through the existing
Odoo front door and its verified helpdesk profile. Preview is the default;
`--apply` publishes a selected ticket or internal note. Stable external ids
are imported transactionally. No profile, password, API key or admin CLI is
forwarded to an OMP session. This is not an unattended service-account connector.
Reports must be selected and reviewed before applying; pattern redaction is
best-effort and does not prove the absence of sensitive business content.

The shared Next Sentry configuration has environment/release tags and disables
default PII. Docker build args and Turbo build inputs now preserve the telemetry
configuration; source-map authentication uses an optional BuildKit secret.
A disposable real Next SDK browser/server/edge acceptance fixture passed. Each
run has a unique fingerprint/release plus inspected-head, dirty-source and input
hash metadata, so old evidence cannot satisfy a later run. This does not verify
source-map upload or a deployed business app. It adds no production error route.

## Separate Kodemeio estate and remaining automation work

No Gatus/GlitchTip registration or container was found on the two reachable
Kodemeio hosts. An unregistered monitor elsewhere is not ruled out. Available
memory was approximately 1.1 GiB on kod-prod-02 and 1.6 GiB on kod-prod-01.
An independent monitoring host has not been selected. Do not provision a new
paid host or change production desired state without recording its target and
the human's acceptance of the concrete deployment plan.
The candidate and remaining host/routing/staging acceptance steps are in
[incident-triage-rollout.md](incident-triage-rollout.md#prepared-cloud-candidate-not-deployed).

The inventory also found two unreachable server records, a VISION initialization
failure and an unhealthy DSH Authentik outpost. These remain separate production
faults; no credentials, routes or legacy services have been changed.
Desk records these separately as
[HT00531 — VISION initialization](https://desk.idtpp.com/web#id=531&model=helpdesk.ticket&view_type=form)
and [HT00532 — outpost dependency](https://desk.idtpp.com/web#id=532&model=helpdesk.ticket&view_type=form).
Existing ticket titles were searched first; no matching VISION/outpost bug was
found. Their descriptions require staging reproduction/review and human
production acceptance. No fix has been deployed.

Live Hatchet job acceptance, real staging application instrumentation/source-map
upload, a scoped Desk service identity, contained automatic OMP dispatch and
enforced provider spending stops still require acceptance. The evidence collector
does not dispatch agents. Local success does not establish cloud readiness.
