# TPP service monitoring

The live Gatus configuration includes 19 HTTPS checks and 42 Compose runtime
checks. It covers all deployed TPP and TPP25 Compose stacks found on prod01,
prod03, prod04, prod06, and prod07 on 2026-10-07. `coverage.json` records the
required services, successful init jobs, and services with native health checks.
Outline and the retired Jitsi EU rollback stack are idle and excluded. No active
TPP n8n stack was found.

Mailcow has a web check and a runtime check for all 18 components. Jitsi has a
web check and runtime checks for web, Prosody, Jicofo, JVB, and coturn. These
checks do not prove email delivery or a complete audio/video call. PostgreSQL
and PgBouncer use their existing Docker health checks; unconfigured service
health checks prove container runtime only. Backup containers being running
does not prove a recent successful backup or restoration.

`host_health.py` runs every 60 seconds using a root-owned systemd timer on each
host. It reads only Compose project/service labels, runtime state, exit codes,
and health status. It never collects environment values or health-check logs.
The reviewed configuration uses a 90-second Gatus heartbeat interval. Since
Gatus uses this for both expiry and polling, the first failed dashboard result
appears 90–180 seconds after the last push under normal scheduling. Incident
notifications additionally require the configured three consecutive failures.
The 2026-10-07 deployment evidence predates this review fix and used 180-second
intervals; apply the updated configuration to enforce the shorter bound. Each host
has its own bearer, valid only for that host's reviewed endpoints. The exact
POST paths are routed through Traefik; Gatus checks each bearer. Docker and
provider credentials are not given to Sentinel.

A one-shot init container that has already exited successfully still counts as a
failure until it is named in the target's `init_services`. The production ERP on
prod03 keeps a completed `odoo-init` container in its Compose project, so the
`tpp-odoo-erp` target must declare `init_services: ["odoo-init"]`, exactly as
staging does. Without it the `tpp-odoo-erp` runtime heartbeat is permanently red
while `odoo-web`, `odoo-gevent`, and `odoo-cron` are all healthy.

Operator installation goes through `./dokploy.sh monitoring-remote`, with an
explicit preview and `--apply`. Configuration lives under
`/etc/kodemeio/gatus-exporter/config.json` (0600). Units are
`kodemeio-gatus-health.service` and `kodemeio-gatus-health.timer`. The service
runs a repository-matching script and has no writable system directories.

When changing monitoring configuration, regenerate the config revision in all
three monitoring services, install both config files, preserve/push the complete
live environment, and deploy through Dokploy. Preserve the machine API, health,
webhook, and Authentik routes. Any new alert endpoint must also be mapped in
`services.json`. Native Basic authentication remains for approved machine reads;
public browser access uses Authentik and the Authentik Admins policy.

Sentinel's trusted operations adapter has a fixed 61-endpoint allowlist. Reads
use one authenticated native request with one current result per endpoint, omit
body/error text and secrets, and use existing pagination (50 rows per response). It has no arbitrary target URL,
provider profile, or host push token.

React ERP HTTPS and runtime alerts use distinct webhook endpoint aliases, mapped
to the same service. Collector incident identity includes that alias, so one
check recovering cannot close the other check's incident. The runtime alert body
override preserves its native Gatus endpoint name and Sentinel API key.
