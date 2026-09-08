# Maintenance fallback

A branded **503** maintenance page for any host on a Dokploy server whose
application is down, instead of Traefik's bare `404 page not found`.

Design and the full safety analysis:
[`docs/superpowers/specs/2026-09-08-maintenance-page-design.md`](../../docs/superpowers/specs/2026-09-08-maintenance-page-design.md).

## Why a 404 appears at all

Dokploy publishes each domain as **Traefik labels on the application container
itself** (`provider=docker`) — there is no file-provider entry for app domains.
When a deploy removes the container, the router goes with it and the host falls
through to Traefik's global 404. Measured by stopping one container:

| container | response |
|---|---|
| running | `301` |
| **stopped** | **`404 page not found`** — identical to a host with no router |
| restored | `301` |

`compose/odoo.prod.yml` makes the window long by design: `odoo-web` waits on
`odoo-init → service_completed_successfully`, so no web container exists for the
whole init/upgrade.

## How the fallback wins only when it should

Two catch-all routers at **`priority=1`**. A real app's `Host(...)` router is
priority 30 (Traefik derives it from rule length), so the fallback is invisible
while anything is up and answers only where Traefik already answered 404.

**Deploy one per server, not one per app.** Traefik is per Dokploy server, and a
catch-all covers every domain on that server — present and future.

## Deploying it

There is no `domain:` for this app and there must never be one; it owns no Host
router. Because the compose content is raw rather than git-backed, it is created
through `compose create -f` rather than `deploy apply`:

```bash
./dokploy.sh <platform> compose create <environmentId> \
  --name <tenant>-infra-maintenance \
  --server <serverId> \
  -f deploys/maintenance/docker-compose.yml \
  --no-auto-deploy --yes

# 🔴 Both of the following are REQUIRED — see the two traps below.
./dokploy.sh <platform> compose update <composeId> --no-auto-deploy --yes
./dokploy.sh <platform> compose update <composeId> --source-type raw --yes

./dokploy.sh <platform> compose start <composeId> --yes
```

### 🔴 Two Dokploy traps, both hit on the first real deploy (2026-09-08)

1. **`--no-auto-deploy` on `compose create` does not stick.** The service was
   created with `autoDeploy: True` regardless. Left alone, a git push can trigger
   a git-clone rebuild of a service that has no repository. Fix it with a
   separate `compose update --no-auto-deploy` and **re-read the record** to
   confirm — do not trust the create.
2. **`compose create -f` stores the file but leaves `sourceType: github`.** The
   content is there (verified by byte count and by grepping the stored
   `composeFile`), but the source type contradicts it. Set `--source-type raw`
   in its own call.

Both are silent. Always re-read with `compose get` and check
`sourceType`, `autoDeploy`, `domains: []`, and that the stored `composeFile`
still contains the nine `traefik.http.*` labels.

Passing several fields to `compose update` in one call returned
`APIError (400): Input validation failed`. One field per call works.

## Verifying it

An unrouted host has **no certificate**, so an HTTPS probe of one fails the TLS
handshake before HTTP is reached and returns nothing — that is not evidence the
fallback is down. Probe over HTTP, or use `-k`:

```bash
IP=<server ip>
curl -sI -H 'Host: nosuchhost.example.com' "http://$IP/"      # 503 + X-Maintenance
curl -skI --resolve nosuchhost.example.com:443:$IP https://nosuchhost.example.com/
curl -sI -H 'Host: <a real host>' "http://$IP/"               # 308 — NOT hijacked
```

The real case — a host that *has* a cert but whose container is gone — keeps its
certificate, so the page is served over valid TLS.

## Editing the page

Change `docker-compose.yml`, then push the content and redeploy. There is no
image build and no registry:

```bash
./dokploy.sh <platform> compose update <composeId> --compose-file deploys/maintenance/docker-compose.yml --yes
./dokploy.sh <platform> compose redeploy <composeId> --yes
```

🔴 **`compose import -f` does NOT work here — it returns
`APIError (400): Input validation failed` and writes nothing.** It fails
*silently* in a loop, because a redeploy afterwards still succeeds and simply
redeploys the old content, so the change looks applied and is not. Use
`compose update --compose-file`, and **verify by byte count** before redeploying:

```bash
./dokploy.sh <platform> --json compose get <composeId> | python3 -c "
import json,sys
t=sys.stdin.read(); d=json.loads(t[t.find('{'):])
c=d.get('composeFile') or ''
local=open('deploys/maintenance/docker-compose.yml').read()
print(f'stored={len(c)} local={len(local)} MATCH={len(c)==len(local)}')"
```

🔴 **Compare characters to characters.** This file contains non-ASCII (`🔴`,
`—`), so `wc -c` (bytes, 11017) and `len(open(f).read())` (characters, 10669)
disagree by 348 on the same file. A verification that mixes the two never
matches and teaches you to ignore it.

Read the four `🔴` rules at the top of `docker-compose.yml` first. The sharpest:
**never add `ports:`** — a published `:80`/`:443` would contend with Traefik for
the host ports and could take every site on that server down.

## Rollback

Remove the Dokploy app, or stop it. Routing returns to today's 404 within one
Traefik watch interval. No Odoo instance is touched at any point.
