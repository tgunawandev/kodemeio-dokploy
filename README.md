# kodemeio-dokploy

Source of truth for Kodemeio services operated through Dokploy: deployment
manifests, environment contracts, infrastructure, monitoring, and runbooks.

The executable CLI is maintained separately in
[kodemeio-skills](https://github.com/tgunawandev/kodemeio-skills). This repository
consumes `kctl-dokploy`; it does not contain CLI implementation code.

## Repository layout

```text
deploys/
├── bases/                  # Reusable manifest bases
├── bootstrap/              # Dokploy and Traefik bootstrap assets
├── composes/               # Versioned raw Compose definitions
├── env/                    # Gitignored values + committed .example contracts
├── instances/              # local, staging, and production desired state
├── migrations/             # Server/application migration manifests
├── schema/                 # Manifest schema ownership notes
├── setup/                  # Post-deployment company setup
├── tenants/                # Tenant definitions used by the generator
├── tests/                  # Offline generator and manifest tests
└── generate.py

infra/
├── modules/                # Cloudflare and Hetzner Terraform modules
└── *.tf                    # Root infrastructure configuration

ops/
├── monitoring/             # Alerts, Gatus, Grafana, and apply scripts
├── onboarding/             # Local onboarding execution logs (gitignored)
├── runbooks/               # Incident and recovery procedures
└── scripts/                # Inventory, parity, backup, and refresh tools

docs/
├── adrs/                   # Architecture decisions
├── architecture.md
├── operations/
└── archive/                # Historical CLI and implementation plans

legacy/dk-shell/            # Pointer to the archived shell toolkit
```

## Tooling

Prerequisites:

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- `kctl-dokploy==0.16.6`
- Terraform 1.5+ for infrastructure validation

Install repository dependencies:

```bash
uv sync
uv tool install "kctl-dokploy==0.16.6"
```

Every Dokploy invocation must select an explicit profile:

```bash
kctl-dokploy -p <profile> doctor ai-summary
kctl-dokploy -p <profile> deploy validate \
  -f deploys/instances/production/mac-react-sfa.yaml
kctl-dokploy -p <profile> deploy apply \
  -f deploys/instances/production/mac-react-sfa.yaml --dry-run
```

Run the local quality gate:

```bash
just check
```

## Deployment flow

The declarative pipeline is:

```text
validate → preflight → DNS → database → registry → compose → environment
→ domain → deploy → verify → backup → schedules → post-deploy
```

Production execution is manual and environment-protected. Pull requests run
offline validation; deployment requires an explicit manifest, profile, and
GitHub environment.

## Safety invariants

- Never commit real `.env` files. Commit only sanitized `.env.*.example` files.
- Standard HTTP services join the external `dokploy-network` and route through
  Traefik domains.
- Do not publish HTTP service ports directly unless a documented protocol
  exception requires it.
- Never stop or remove the `dokploy` or `traefik` platform containers through
  repository automation.
- Treat deployment submission as asynchronous and wait for verification before
  reporting success.

## Documentation

- [Architecture](docs/architecture.md)
- [Migration SOP](docs/migration-sop.md)
- [Jitsi Singapore migration report](ops/runbooks/jitsi-sg-migration-execution-2026-10-05.md)
- [PostgreSQL restore](ops/runbooks/postgres-restore.md)
- [Repository consolidation ADR](docs/adrs/0001-dokploy-consolidation.md)
- [Contributing](CONTRIBUTING.md)
