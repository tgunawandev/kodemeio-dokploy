# IDTPP production monitoring

Activated 2026-10-05 under the operator's explicit production-monitoring authorization.
This is separate from the `kodeme.io` estate. Never stop legacy services based on this rollout.

| Service | Host | Dokploy compose | Version |
| --- | --- | --- | --- |
| Gatus + incident ingress/collector | tpp-prod-06, 5.223.69.142 | RuWNNe3JCuQ8c_SDgjY-8 | Gatus 5.37.0; collector image below |
| Existing GlitchTip | tpp-prod-01, 178.104.127.104 | p1m6OF1PtiVaYyYiriHjY | Existing v4.1 web/worker/Redis; database preserved |

URLs: https://gatus.idtpp.com and https://glitchtip.idtpp.com.
Gatus `/health` is public; native status API reads require Basic authentication. Browser
access to Gatus and GlitchTip uses Authentik forward authentication restricted to active
Authentik Admins. Gatus injects its private upstream Basic credential after browser authorization.
The private workstation operator file is `deploys/env/production/.env.tpp-monitoring-operator`
(mode 0600), with `GATUS_USERNAME` and `GATUS_PASSWORD`. It is ignored, never committed.
The password is not uploaded to Gatus: only its bcrypt-base64 hash is supplied.
Use existing GlitchTip human/SSO login; the collector identity has no usable login password.

`gatus.json` now checks 26 verified HTTPS endpoints every minute, requiring status 200
(and, for the web checks, at least 14 days of certificate validity). An additional 41 runtime checks receive
reviewed Compose readiness heartbeats: 67 checks in all. 60 of them produce incidents
(see [current coverage](COVERAGE.md) and the limits of runtime-only monitoring); the
other 7 are Mattermost-only (below). For an incident-producing check, three failures
trigger evidence intake and two successes send recovery.

**Human alerts go to Mattermost `IT Odoo`** (team TPP, channel `it`) through Gatus's
`mattermost` provider, for the Odoo checks only: the existing `tpp-erp` web check plus
seven Mattermost-only checks, the web health of tpp25, mac and mac-hrms prod and the
background-job health (`/queue_runner_guard/health`, which must answer `200 ok`; a 503
body names `stalled`, `failing` or `cron-late`) of tpp, tpp25, mac and mac-hrms prod.
The incoming-webhook URL is the secret `GATUS_MM_ODOO_IT_WEBHOOK` in the ignored
`.env.tpp-infra-gatus`; the webhook is "Gatus - Odoo health" on that channel. A
Mattermost-only check opens no incident, so it needs no `services.json` mapping and no
Sentinel allowlist slot; `tests/test_host_health.py` counts only `custom` alerts.
Gatus debounces at three consecutive failed checks (about three minutes at 60 s). The
queue check's `failing` state lasts an hour after ANY job failure (Odoo's
`queue_runner_guard.health_failed_per_hour`, default 1), so one failed job alerts
the channel once, and the resolution follows about an hour later. `services.json` maps those health services and the eight existing Odoo GlitchTip
projects, with exact project IDs and staging/production environments. Events without the
configured environment tag are refused rather than assigned to an environment by guess.

The new GlitchTip Member has access to the nine configured projects and a dedicated token
with only `org:read`, `project:read`, `event:read`. Native project alerts POST references to
an authenticated receiver on Gatus's domain. Proxy access logging and tracing are disabled
on this token-bearing webhook router. The collector polls missed unresolved issues and
selects bounded redacted evidence; it receives no Desk, model, GitHub or deployment keys.

The production collector uses the same tested image as the local pilot:
`sha256:2d8da50510f415cdd8a3c83593fee7964d0af580242220a15ec226863bc0f9b7`.
Local GlitchTip 6.2.6 and existing production v4.1 intentionally differ; this rollout does
not approve a live database upgrade. Production selects `issue_api: legacy` with an exact
project ID; local uses organization-scoped routes. Both enforce project/environment scope.
This is an inspected local build, not a published clean Git release or remote CI assertion.

## Reproduce through the operator front door

Keep `KCTL_DOKPLOY_PROFILE=idtpp` explicit. `monitoring-remote` is an operator transport;
never expose it or the operator profiles to an agent session. First load the reviewed image on host06:

```sh
docker save kodemeio-incident-triage:local -o /private/path/collector.tar
./dokploy.sh monitoring-remote idtpp 2-3nUPC6FJqt7QWabzFp1 load-image
./dokploy.sh monitoring-remote idtpp 2-3nUPC6FJqt7QWabzFp1 load-image --apply < /private/path/collector.tar
```

Install the two JSON configuration files with `monitoring-remote ... install-config`:
stdin is an object mapping only `gatus.json` and `services.json` to their file contents.
The helper writes only `/etc/dokploy/monitoring/tpp-infra-gatus/` on the selected server.
File-backed Compose configs preserve read-only container filesystems; inline `content`
configs cannot be created in a read-only service on this host's Docker Compose 5.1.4.
Bump `MONITORING_CONFIG_REVISION` to the configuration content hash when files change, so
Compose recreates readers; changing the host file alone does not reload process state.

Preview each Dokploy mutation with the identical command plus `--yes --dry-run`.
Update only the new compose with `compose update ... --compose-file
ops/monitoring/idtpp/docker-compose.yml --source-type raw`, push the ignored
`.env.tpp-infra-gatus` through `env push`, then submit `compose start`.
Verify the asynchronous deployment is `done`, TLS validates, all checks pass, containers
are healthy, and the protected operations API reports a fresh heartbeat and drained queue.
Do not use the generic manifest deployment orchestrator for this raw compose: its current
`phase_compose` hardcodes GitHub source even when a manifest says `type: raw`.
These tracked files and scoped commands are the source for this deployment until that CLI
supports raw-source reconciliation. No misleading manifest is registered as deployable.

Persistent volumes belong to this compose only. Evidence stays on a mode-0700 state
volume; unresolved incidents are retained. Back up the SQLite database online through
`Store.backup()` and verify restoration to a different private path before retiring data.
No Docker socket, application source, founder home or host key is mounted into these services.

## Acceptance and remaining work

Verified production canary: `INC-d369f850454f68458598e48c`, actual GlitchTip event ingestion,
native webhook and selected issue/exception frames, environment `production`, release
`synthetic-prod-activation-20261005`, linked solely to rollout ticket HT00530. Queue drained,
zero quarantine, fresh heartbeat. Do not route unrelated faults to that rollout ticket.

Desk remains the canonical ticket system: preserve an existing human report and record
triage, fixes, tests, independent review and explicit human production acceptance there.
Use the operator bridge `../incidents/desk.py`; inspect selected evidence before publishing.
The collector does not automatically create tickets or dispatch agents. A dedicated unattended
Desk identity, contained model dispatch, spending stops, real staging SDK/source-map acceptance
and an independent external dead-man alarm remain follow-up work. Installing monitoring does
not prove every application's SDK is sending events or fix the meeting execution defect.
