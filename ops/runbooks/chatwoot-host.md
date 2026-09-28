# CW1 — host provisioning (M1)

**Scope:** provisioning the new host that runs Chatwoot + kido_chat + the
order_intake ingress for Terakidz's WhatsApp inbox. This is the FIRST gate —
nothing else in the CW1 rollout can start before this host exists and is
registered in Dokploy.

- Design: CW1 Chatwoot design, 2026-09-26
  (D11 "Chatwoot deployment", §8 gate M1)
- Manifest: `deploys/instances/production/kod-app-chatwoot.yaml`
  (`server: kod-cw1-host` — a placeholder; replace it once this runbook is done)
- Next: `chatwoot-meta-whatsapp.md` (M2/M3), then `chatwoot-rollout.md` (M6/M7)

## Why a new host, not `kod-prod-01`/`kod-prod-02`

The spec (D11) is explicit: this is a **new** host, not one of the existing
shared `kod` hosts. Two independent reasons:

1. **Sizing.** Chatwoot alone needs ≥4 vCPU / 8 GB for self-hosted use up to
   ~10k conversations/day (Chatwoot's own deployment requirements — 4 GB
   floor plus 1 GB swap headroom during upgrades), before counting
   kido_chat's ingress + Hatchet worker or the order_intake ingress +
   worker. Co-locating on an already-loaded host risks starving both.
2. **Blast radius.** This host will eventually be reachable from the public
   internet (Meta's WhatsApp Cloud webhooks must reach Chatwoot). Keeping it
   off the same host as `kod-infra-*` services (Authentik, LiteLLM, the
   kctl toolbox) means a Chatwoot-side incident cannot pivot into anything
   else on the kod estate.

Also (per `kod-infra-gatus.yaml`'s own comment on choosing a monitor host):
this must **not** become the host anything else's "is the estate up"
monitoring depends on, for the same reason a fire alarm doesn't live in the
room most likely to catch fire.

## Sizing

Per spec D11 / Chatwoot's own requirements (D1): **≥4 vCPU / 8 GB RAM**
recommended. Breakdown this stack needs room for:

| Component | Rough floor |
|---|---|
| Chatwoot web (Puma) | 2 CPU / 2 GB (compose limit) |
| Chatwoot worker (Sidekiq) | 2 CPU / 2 GB (compose limit) |
| Chatwoot's own Postgres (pgvector) | 1 CPU / 1 GB (compose limit) |
| Chatwoot's own Redis | 0.5 CPU / 512 MB (compose limit) |
| kido_chat ingress + Hatchet worker | 1.5 CPU / 768 MB combined |
| order_intake ingress + Hatchet worker | 1.5 CPU / 768 MB combined |
| OS + Dokploy agent + headroom | ~1 vCPU / 1 GB |

These are the exact `deploy.resources.limits` in
`kodemeio-chatwoot/docker-compose.prod.yml` — a host at the 4 vCPU / 8 GB
floor is running at its ceiling with no burst room; 6 vCPU / 12 GB gives
real headroom for WhatsApp traffic spikes and Chatwoot upgrades (which
themselves need +1 GB swap per D1).

## Provisioning steps

1. **Provision the server** (Hetzner, `kodemeio` Hetzner project — per
   infrastructure rules, never mix into the `idtpp`/`tpp`/`mac` project).
   Use `kctl-hz` per the hetzner-admin skill. Record the server name using
   the estate's `kod-` naming convention (e.g. `kod-cw1-01` — replace the
   manifest's `kod-cw1-host` placeholder with the real name once chosen).
2. **Join `dokploy-network`** (external, per infrastructure rules) once the
   Dokploy agent is installed — this is the ONLY network Chatwoot's web
   service joins from outside its own compose (see
   `docker-compose.prod.yml`'s network comment: everything else is on
   `cw-internal`, no published ports).
3. **Register the server in Dokploy**: `kctl-dokploy -p kodemeio servers add
   <name> --ip <ip>` (see the dokploy-admin skill). Confirm it appears in
   `kctl-dokploy -p kodemeio servers list` before editing the manifest.
4. **Update `kod-app-chatwoot.yaml`**: replace `server: kod-cw1-host` with
   the real, registered server name. Re-run
   `kctl-dokploy -p kodemeio deploy validate --file deploys/instances/production/kod-app-chatwoot.yaml`
   — it must print the new server name and `OK Manifest is valid`.
5. **DNS.** Once the domain decision (spec §8 Q1: `chat.kodeme.io` vs a
   Terakidz-branded domain) is made, update `kod-app-chatwoot.yaml`'s
   `dns.name` / `domain.host` together and follow the normal
   `kctl-dokploy domains create` flow in `chatwoot-rollout.md` (M6) —
   not here; DNS is a rollout step, not a host-provisioning one.
6. **Firewall.** Only 80/443 (Traefik, via Dokploy) need to be reachable from
   the internet. No other port is published by `docker-compose.prod.yml` —
   confirm with `docker compose -f docker-compose.prod.yml config` that no
   service declares a `ports:` key (the compose file must not; this is the
   "no published ports" infra rule, enforced by inspection since Chatwoot
   itself is the only service Meta and staff ever reach directly).

## What this runbook does NOT cover

- Meta/WhatsApp Business setup — `chatwoot-meta-whatsapp.md` (M2/M3).
- Secrets, deploy order, kill switches, rollback — `chatwoot-rollout.md`
  (M6/M7).
- Privacy/consent wording — `chatwoot-privacy.md` (M5, draft for
  counsel).

## Rollback

Provisioning itself has no rollback beyond deleting the server if the
founder changes plans before any real deploy happens — this stack has not
been deployed to it at this stage, so there is nothing running to unwind.
