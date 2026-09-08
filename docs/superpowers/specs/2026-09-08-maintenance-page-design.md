# Maintenance page instead of 404 during a deploy

Status: design approved 2026-09-08 · Phase 1 only (the 404 window)
Owner repo: **kodemeio-dokploy**. Phase 1 changes **nothing** in `kodemeio-odoo`.

## Problem

Every Odoo release shows end users a bare `404 page not found` for the whole
deploy window — measured at ~1m45s for a plain redeploy and 3–7 min when
`odoo-init` runs an upgrade. A 404 reads as "this system is gone", which is the
worst available message for a working ERP that is simply mid-upgrade.

## Root cause — measured, not inferred

Dokploy publishes each domain as **Traefik labels on the application container
itself** (`provider=docker`). There is no file-provider entry for app domains;
`/etc/dokploy/traefik/dynamic/` holds only `dokploy-ui.yml`, `dokploy.yml`,
`middlewares.yml` and `wildcard-local.yml`.

```
traefik.http.routers.<app>-<domainId>-websecure.rule = Host(`<host>`)   # priority 30
```

When a deploy removes the container, the router is removed with it and the host
falls through to Traefik's global 404. Verified on the live Dokploy/Traefik rig
by stopping and restoring one container:

| container state | `Host: whoami.local.kodeme.io` |
|---|---|
| running | `301` |
| **stopped** | **`404 page not found`** — byte-identical to a host with no router at all |
| restored | `301` |

`compose/odoo.prod.yml` makes the window long by design: `odoo-web` has
`depends_on: odoo-init → service_completed_successfully`, so no web container
exists for the entire init/upgrade.

Two distinct bad states, which need different fixes:

1. **404** — container gone, router gone. Minutes. **This spec.**
2. **502** — container up, Odoo still loading its registry (~50–90 s on the
   406-module tpp DB). Router exists, backend not listening. **Deferred.**

## Design

One always-on static service per Dokploy server, deployed as an ordinary Dokploy
compose app, owning two Traefik routers at **`priority=1`**:

```
traefik.enable                                             = true
traefik.docker.network                                     = dokploy-network
traefik.http.services.maintenance.loadbalancer.server.port = 80
traefik.http.routers.maintenance-web.rule                  = HostRegexp(`^.+$`)
traefik.http.routers.maintenance-web.priority              = 1
traefik.http.routers.maintenance-web.entrypoints           = web
traefik.http.routers.maintenance-web.service               = maintenance
traefik.http.routers.maintenance-sec.rule                  = HostRegexp(`^.+$`)
traefik.http.routers.maintenance-sec.priority              = 1
traefik.http.routers.maintenance-sec.entrypoints           = websecure
traefik.http.routers.maintenance-sec.service               = maintenance
```

Traefik serves the highest-priority matching router. A live app is 30, so the
fallback is invisible until that app's container disappears — then it answers on
**the same URL**. No redirect: when the deploy finishes the user's refresh lands
exactly where they were.

Stock `nginx:1.27-alpine`. **No custom image, no registry, no build pipeline** —
config and page inlined in the manifest's compose, so changing the copy is a
manifest apply, not a build.

Catch-all rather than one router per host: it covers every current and future
domain on the server with no enumeration, so `deploys/generate.py` needs no
change and a new instance is protected the day it ships.

## Why this cannot take Odoo down

The stated constraint. Each claim below is either measured on the rig or a
structural property of the change.

| Risk | Why it cannot happen |
|---|---|
| Steals traffic from a healthy Odoo | Priority 1 vs 30. **Measured**: with the fallback live, `whoami` and `dbgate` still returned 301/200. Even a *forgotten* priority label is safe — `HostRegexp(^.+$)` is 21 characters, so Traefik's length-derived default is 21, still below 30. |
| Restarts or disturbs Odoo containers | Separate Dokploy app, separate compose project. `docker compose up -d` touches only its own project. Phase 1 modifies no Odoo file, triggers no image build, and requires no Odoo redeploy. |
| Fights Traefik for :80/:443 | **Hard rule: the compose declares no `ports:`.** Traefik reaches it over `dokploy-network`. A published port here would contend for the host ports and could take every site on the server down. |
| Breaks routing for other apps | Traefik validates labels per container. A malformed label invalidates only our own router. We declare no entrypoint override, no TLS store, no default certificate, and no middleware that another router references. |
| Triggers ACME / Let's Encrypt rate limits | `HostRegexp` yields no domain, so Traefik requests no certificate. **Measured**: over the whole spike window Traefik logged exactly one ACME line — the challenge lookup we issued ourselves, answered by `acme-http@internal`. Zero cert requests caused by the catch-all. |
| Breaks the ACME HTTP-01 challenge | `acme-http@internal` runs at priority `9223372036854775807`. **Measured**: `/.well-known/acme-challenge/<token>` was answered by the ACME router, not the 503 page, with the fallback live. |
| Serves the maintenance page over a bad certificate | TLS is served from the existing Let's Encrypt cert by SNI. Corroborated by current fleet behaviour: `poll_health()` (`bin/odoo/release:151`) runs plain `curl` with no `-k` and returns **404** during every deploy — an invalid or missing cert would give `000`. **Confirmed live on staging before any prod rollout.** |
| Starves the server | `nginx:1.27-alpine`, `restart: unless-stopped`, explicit cpu/memory limits per the infrastructure rules. |
| Leaves residue if abandoned | **Measured**: after teardown, the unrouted host returned to 404 and the live app to 301. Rollback is removing one Dokploy app; Odoo is never touched. |

Worst realistic failure is that the maintenance container itself dies — which
returns the exact behaviour we have today, a 404. No worse than the status quo.

## Hard rules for implementation

- **No `ports:` in the maintenance compose.** Traefik labels only.
- **Never** edit Traefik static config, and never stop or remove the `dokploy`
  or `traefik` containers.
- **`priority=1` explicit on both routers**, asserted by a test.
- No `tls.stores`, no `defaultCertificate`, no entrypoint-level middleware.
- `autoDeploy` off, per the repo rule for pre-built images.
- Every production rollout step needs its own fresh confirmation.

## Content contract

- **Always 503**, never 200. `poll_health()` accepts only `200` and Gatus expects
  200; a 200 maintenance page would report every deploy as healthy.
- `Retry-After: 30`, `Cache-Control: no-store`.
- Same URL, no redirect.
- **Content negotiation** so an open SPA and the PWAs do not parse an HTML error:
  - `/web/dataset/*`, `/jsonrpc` → JSON-RPC error envelope
  - `/*/api/*` → JSON
  - everything else → HTML
- **Copy**: Indonesian primary, English secondary, plain language
  ("Sistem sedang dalam pemeliharaan"). Auto-retry ~30 s with jitter; the page
  returns to the app by itself when Odoo is back.
- `/.well-known/*` is never ours — the ACME router already outranks us.

## Scope

Four production servers carry every Odoo and PWA domain:

| server | domains | Odoo instances |
|---|---|---|
| `tpp-prod-03` | 11 | tpp-odoo-erp, tpp-odoo-hrms, tpp-odoo-helpdesk, tpp-odoo-erp-light |
| `tpp-prod-02` | 6 | mac-odoo-erp, mac-odoo-hrms, mac-odoo-erp-light |
| `kod-prod-02` | 21 | kod-odoo-full |
| `tpp-prod-07` | 1 | tpp25-odoo-erp |

Plus `tpp-stg-01` for staging.

**Accepted consequence:** a catch-all protects *every* domain on the server, not
only Odoo — on `tpp-prod-03` that includes `idtpp.com`, `rmm.idtpp.com` and
`mdm.idtpp.com`. Approved as a feature.

## Testing

Acceptance is the four-case replay, run on the local rig and again on staging:

1. app running → app answers, unchanged
2. app container stopped → 503 + maintenance page + `Retry-After`
3. `/.well-known/acme-challenge/<token>` → answered by the ACME router
4. app restored → app answers again, no manual step

Then the real thing: deploy on `tpp-stg-01`, run an actual addon upgrade on tpp
staging, and capture status codes across the whole window. Must be 503
throughout, never 404.

**The revert that must go red:** remove the maintenance app and re-run the
identical capture. It **must** show `404 page not found`. A capture that passes
with the feature removed proves nothing.

## Rollout

`tpp-stg-01` → confirm TLS is the real Let's Encrypt cert during a live window →
`tpp-prod-03` → the remaining three. Fresh confirmation before each production
step. No Odoo restart at any step, so the deploy-window restriction does not
apply — but this is still a change on a production server.

## Rollback

Remove the Dokploy app, or `docker compose down` on that project. Routing returns
to today's behaviour within one Traefik watch interval. Measured clean on the rig.

## Out of scope

- **Phase 2 — the 502 tail** (~60–90 s while Odoo loads its registry). Needs a
  Traefik `errors` middleware defined on the always-up maintenance container plus
  a router we own in `compose/odoo.prod.yml`, mirroring the existing `-ws` router
  at priority 300. Requires an image build and a release on every instance.
- **Phase 3 — armed message.** `./odoo.sh release` setting "planned upgrade, back
  by ~12:45" before it starts and clearing it after, so a planned deploy reads
  differently from a crash.
