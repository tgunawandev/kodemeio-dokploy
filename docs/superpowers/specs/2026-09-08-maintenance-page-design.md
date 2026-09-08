# Maintenance page instead of 404 during a deploy

Status: **Phase 1 LIVE on all five tpp servers, 2026-09-08.** Phase 2 half-shipped (middleware defined, router not yet enabled).
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
traefik.http.routers.maintenance-web.rule                  = HostRegexp(`.+`)
traefik.http.routers.maintenance-web.priority              = 1
traefik.http.routers.maintenance-web.entrypoints           = web
traefik.http.routers.maintenance-web.service               = maintenance
traefik.http.routers.maintenance-sec.rule                  = HostRegexp(`.+`)
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
| Steals traffic from a healthy Odoo | Priority 1 vs 30. **Measured**: with the fallback live, `whoami` and `dbgate` still returned 301/200. Even a *forgotten* priority label is safe — `HostRegexp(`.+`)` is 18 characters, so Traefik's length-derived default is 18, still below 30. |
| Restarts or disturbs Odoo containers | Separate Dokploy app, separate compose project. `docker compose up -d` touches only its own project. Phase 1 modifies no Odoo file, triggers no image build, and requires no Odoo redeploy. |
| Fights Traefik for :80/:443 | **Hard rule: the compose declares no `ports:`.** Traefik reaches it over `dokploy-network`. A published port here would contend for the host ports and could take every site on the server down. |
| Breaks routing for other apps | Traefik validates labels per container. A malformed label invalidates only our own router. We declare no entrypoint override, no TLS store, no default certificate, and no middleware that another router references. |
| Triggers ACME / Let's Encrypt rate limits | `HostRegexp` yields no domain, so Traefik requests no certificate. **Measured**: over the whole spike window Traefik logged exactly one ACME line — the challenge lookup we issued ourselves, answered by `acme-http@internal`. Zero cert requests caused by the catch-all. |
| Breaks the ACME HTTP-01 challenge | `acme-http@internal` runs at priority `9223372036854775807`. **Measured**: `/.well-known/acme-challenge/<token>` was answered by the ACME router, not the 503 page, with the fallback live. |
| Serves the maintenance page over a bad certificate | **Measured live during a real `tpp staging` addon upgrade, 2026-09-08.** With the router gone and the page being served, a plain `curl` with no `-k` returned `http=503 ssl_verify=0`, and the presented certificate was `CN=tpp-odoo-erp-stg.idtpp.com`, issuer `Let's Encrypt YR2` — the real production certificate, not a default or self-signed one. Traefik keeps serving a stored ACME certificate by SNI after the router that requested it disappears. **Caveat:** a host that never had a certificate issued (a typo, an undeployed domain) has none to serve, so an HTTPS probe of one fails the handshake before HTTP — that is expected, and is not the case this feature targets. |
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

### Rolled out 2026-09-08 — every tpp server, verified `404 -> 503`

| server | compose | composeId |
|---|---|---|
| `tpp-prod-01` | `tpp-infra-maintenance-prod01` | `Pt3LdHhmJdEID1-xPjNyG` |
| `tpp-prod-03` | `tpp-infra-maintenance-prod03` | `X-G1ET5jvRvSrKqWJc4hN` |
| `tpp-prod-04` | `tpp-infra-maintenance-prod04` | `uGikL1Hp8BhVhcB5ssgRJ` |
| `tpp-prod-06` | `tpp-infra-maintenance-prod06` | `0ryky5TpEpB9ZXEUDB_d2` |
| `tpp-prod-07` | `tpp-infra-maintenance` | `aQ5yl8FCqZxjTdIUc8dfg` |

### Not rolled out

| server | domains | Odoo instances |
|---|---|---|
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

### Result — first live run, 2026-09-08

Deployed on **tpp-prod-07** (where tpp staging actually lives — there is no
`tpp-stg-01` server in Dokploy, despite 14 staging manifests naming one) and
driven by a real `./odoo.sh addon tpp staging upgrade base_management --yes`.
Sampled every 3 s, 116 samples:

```
01:47:49  target=200  maint=0   tpp25prod=200    Odoo up
01:49:27  ---------------- deploy window opens ----------------
01:49:32  target=503  maint=1   tpp25prod=200    maintenance page
01:56:24  target=200  maint=0   tpp25prod=200    returned by itself
```

| assertion | result |
|---|---|
| 404 seen at any point | **0 of 116** |
| window covered by the page | 01:49:32 -> 01:56:24, **~6m52s** |
| return to service | automatic, no manual step |
| TLS during the window | `ssl_verify=0`, `CN=tpp-odoo-erp-stg.idtpp.com`, Let's Encrypt YR2 |
| `tpp25-odoo-erp` **production** on the same Traefik | **0 of 116 non-200** |
| `release` health gate | logged `HTTP 503` for 7 min, then `Healthy after 7m30s`, exit 0 |

That last row is the before/after: `release` previously logged **404** through
this same phase (CLAUDE.md: "404 during a deploy is normal for 3-7 min"). Same
tool, same phase, now 503 — and the gate still behaved correctly, confirming
that 503-never-200 does not fool the tooling.

### What this run also proved is NOT covered

The same session produced a **500 Internal Server Error** on
`tpp-odoo-erp-stg.idtpp.com/odoo`, and the fallback correctly did **not**
intercept it: Odoo was up, its router matched, and it served its own Werkzeug
error page.

Cause was unrelated to this feature — `release … reliable base_management`
shipped the whole new image while upgrading only that module, leaving
`account_payment_advance` at `db=18.0.3.0.0` against `image=18.0.4.0.0`, so
`res_company.advance_transfer_enabled` did not exist. The documented `reliable`
trap; `bin/deploy-preflight` would have blocked it and was not run.

**The lesson for this spec:** a user cannot tell a 404 from a 500 from a 502 —
all three read as "the system is broken". Phase 1 converts only the 404. Phase 2
(the Traefik `errors` middleware) is what converts 5xx from a live-but-broken
Odoo, and this incident is the argument for promoting it rather than deferring
it.

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

- **Phase 3 — armed message.** `./odoo.sh release` setting "planned upgrade, back
  by ~12:45" before it starts and clearing it after, so a planned deploy reads
  differently from a crash.

## Phase 2 — designed and half-shipped, NOT yet enabled

Covers 5xx from a **live but broken** app: the 502 tail while Odoo loads its
registry, and the 500 from a half-upgraded instance seen on 2026-09-08.

**Shipped already (inert).** The `errors` middleware is defined on the
maintenance container itself, so it survives when the app referencing it is gone.
A middleware changes nothing until a router names it, so this is live on all five
tpp servers and currently does nothing.

**Not shipped: the router that names it.** Dokploy's own router cannot carry a
middleware — `compose domains` exposes no such option — so it needs a router we
own, added to `odoo-web` in `compose/odoo.prod.yml`, mirroring the `-ws` router
that already works there:

```yaml
- "traefik.enable=true"
- "traefik.http.services.${COMPOSE_PROJECT_NAME:-odoo}-app.loadbalancer.server.port=8069"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.rule=Host(`${DOMAIN}`)"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.priority=100"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.entrypoints=websecure"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.tls=true"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.tls.certresolver=letsencrypt"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.service=${COMPOSE_PROJECT_NAME:-odoo}-app"
- "traefik.http.routers.${COMPOSE_PROJECT_NAME:-odoo}-app.middlewares=kodemeio-maintenance-errors@docker"
```

Priority 100 beats Dokploy's 30 and loses to the `/websocket` router's 300, so
websocket routing is untouched.

**Measured on the rig, 2026-09-08:**

| case | result |
|---|---|
| app returns 500 through a router carrying the middleware | maintenance page body, upstream status preserved |
| router names a middleware that does **not** exist | Traefik drops only that router; the priority-30 Dokploy router serves the app's own response — **not a 404** |

That second row is why the labels are safe to ship fleet-wide even where the
maintenance container is absent.

### What phase 2 still needs before it goes live

🔴 **It is not a label change you can drop in.** `compose/odoo.prod.yml` is
pulled from the repo at deploy time, so it needs a **redeploy of every Odoo
instance** — and a redeploy runs `pull_policy: always`, so it also pulls a
newer `latest` image. That is exactly what produced the 2026-09-08 outage.

1. `bin/deploy-preflight <tenant> <target>` on every target first, and resolve
   every BLOCK. Skipping it is what broke staging.
2. Prove it on one instance before the fleet — our router replaces Dokploy's for
   normal traffic, so confirm TLS and login on that one instance before going on.
3. A production deploy window, and its own fresh confirmation.
