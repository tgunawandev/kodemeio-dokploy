# CLAUDE.md — kodemeio-dokploy

This repository owns Dokploy deployment desired state, infrastructure,
monitoring, and operational runbooks. CLI implementation belongs in
`kodemeio-skills`.

## Key paths

| Path | Purpose |
|---|---|
| `deploys/bases/` | Reusable deployment bases |
| `deploys/instances/` | Local, staging, and production desired state |
| `deploys/env/` | Ignored values and committed sanitized examples |
| `deploys/tenants/` | Generator inputs |
| `deploys/bootstrap/` | Dokploy/Traefik bootstrap |
| `infra/` | Terraform root and local modules |
| `ops/monitoring/` | Monitoring configuration |
| `ops/runbooks/` | Incident and recovery procedures |
| `ops/scripts/` | Operational utilities |
| `docs/archive/` | Historical, non-authoritative material |

## The front door — `./dokploy.sh`

Everything that touches a Dokploy instance goes through it. It resolves the
platform to a profile, refuses unconfirmed writes, and forwards anything it does
not own to `kctl-dokploy` verbatim, so a CLI command that shipped this morning is
reachable here this afternoon with no registration.

```bash
./dokploy.sh                                  # usage, generated from dokploy.yaml + bin/dokploy/
./dokploy.sh kodemeio applications list       # resolved: kctl-dokploy -p kodemeio ...
./dokploy.sh health kodemeio                  # a repo tool from bin/dokploy/
./dokploy.sh backup idtpp                     # backup status + destination sizes
./dokploy.sh services idtpp --problems        # services that are not `done`
./dokploy.sh hosts idtpp                      # server CPU / memory / disk
./dokploy.sh commands tree                    # passthrough, verbatim
```

| platform | profile | control plane |
|---|---|---|
| `kodemeio` | `kodemeio` | https://dokploy.kodeme.io |
| `idtpp` | `idtpp` | https://dokploy.idtpp.com |
| `abcfood` | `abcfood` | https://dokploy-hz.abcfood.app |
| `local` | `local` | http://localhost:3000 — the only unguarded target |

**`--dry-run` prints the resolved command and runs nothing.** Cheapest possible
first move, and safe against production.

🔴 **`--yes` is the DOOR's confirmation, not the CLI's.** 95 write verbs are
guarded; the door reads the flag then strips it, because `kctl-dokploy` owns no
`--yes` of its own and forwarding one makes it exit on `No such option`.
`--kctl-yes` forces a real one through.

🔴 **`--dry-run` here is the door's, not `kctl-dokploy`'s.** This CLI has a real
`--dry-run` that previews against the live API; reach it with `--kctl-dry-run`.

The guard table in `dokploy.yaml` is **derived**, not hand-written — re-derive it
after a CLI upgrade and check it with:

```bash
../kodemeio-skills/scripts/frontdoor-guards verify dokploy.yaml
```

### One pass — `./dokploy.sh check <platform|all>`

```bash
./dokploy.sh check idtpp                 # every service with its status AND its backup
./dokploy.sh check idtpp --problems      # only what needs attention
./dokploy.sh check all --no-backup       # fleet-wide service status in seconds
./dokploy.sh check all --json            # for a schedule; exits 1 on any problem
```

Reads only. It is a join of `services --json` and `backup --json` with no API
call of its own: one row per service carrying both status and backup verdict,
then the backup detail with the newest object's **date and size**, not just its
age. `all` runs the platforms in parallel, so it costs the slowest one (abcfood,
about four minutes), not the sum. Named `check` because `audit`, `status`,
`report` and `dashboard` are all `kctl-dokploy` groups.

### Backup status — `./dokploy.sh backup <platform>`

```bash
./dokploy.sh backup idtpp                    # every service, every backup, with a verdict
./dokploy.sh backup idtpp --service postgres # narrow it
./dokploy.sh backup abcfood --json           # for a schedule; exits 1 on any problem
```

Reads only. It joins each backup CONFIG to the OBJECTS actually in its S3
destination and reports the age of the newest one, because **`enabled: true` is
not a backup** — it says a schedule exists, not that anything reached the bucket.

It also scans `deploys/instances/` for two silent manifest defects the Dokploy
API cannot show:

🔴 **`backup: {}` and `backup: {enabled: false}` DO NOT DISABLE ANYTHING.**
`InstanceManifest.backup` is `BackupConfig | None` and only `None` makes
`orchestrator.phase_backup()` skip (`orchestrator.py:1006`). Both forms parse to
a `BackupConfig` with `destination=""`, so the phase runs, fails to resolve the
empty destination, and records `failed: Destination '' not found` **on every
deploy**. `BackupConfig` has no `enabled` field at all — pydantic drops the key
silently. **Only `backup: null` works.** Five instances are currently wrong;
`./dokploy.sh backup <platform>` names them.

A missing `backup:` key **inherits the base's block**, which is usually right and
occasionally very wrong — the infra base targets postgres, so an instance with no
database inherits a dump it cannot perform.

### Services and hosts

```bash
./dokploy.sh services idtpp --problems     # only what is not `done`
./dokploy.sh services all --status error   # the whole fleet's failures
./dokploy.sh hosts idtpp                   # CPU, memory, disk, containers per server
./dokploy.sh hosts idtpp --services        # …and which services sit on each host (~18s)
```

🔴 **The status field is `status`, not `composeStatus`.** Both are on the record
and `composeStatus` is always null, so a hand-written
`jq 'select(.composeStatus != "done")'` matches everything and reports the whole
fleet as broken. `services` knows which one is real.

🔴 **`hosts` reads over SSH, not `servers metrics`.** That endpoint returns
`APIError 400` on every server here because the monitoring agent has never been
provisioned — all seven idtpp servers carry an empty
`metricsConfig.server.token`. Run `servers setup-monitoring` if you want the API
path to work. An unreachable host is reported, never skipped; `tpp-prod-05` is
unreachable today.

It is called `hosts` rather than `servers` on purpose: `kctl-dokploy` owns a
`servers` group, and a repo tool by that name would give one typed line two live
meanings. Passthrough `./dokploy.sh <platform> servers list` still works.

### Editing the door

`dokploy.sh`, `bin/dokploy/_boot.sh`, `bin/dokploy/_dispatch.sh` and
`bin/dokploy/health` are **vendored byte-identical** from
`kodemeio-skills/templates/frontdoor/`. Never edit those four in place — edit the
template and run `scripts/frontdoor-sync --write`; `--check` fails on drift.

Everything else in `bin/dokploy/` is **repo-specific and not vendored** —
`backup`, `services`, `hosts` and `check` are those. Drop a new executable in and it becomes a
reserved word automatically; there is no table to register it in.
`dokploy.yaml` is the only per-repo data file. Full standard:
`kodemeio-skills/docs/frontdoor.md`.

## Commands

```bash
uv sync
just test
just lint
just fmt-check
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate

./dokploy.sh <platform> doctor ai-summary
./dokploy.sh <platform> deploy validate -f <manifest>
./dokploy.sh <platform> deploy apply -f <manifest> --dry-run   # preview
./dokploy.sh <platform> deploy apply -f <manifest> --yes       # commit
```

## Rules

Three of these used to be prose asking a human to remember something. They are
now enforced by `./dokploy.sh`, and are kept here to say what enforces them —
**a rule a machine can check should never be only a sentence in a document.**

| Rule | Enforced by |
|---|---|
| Always pass an explicit profile | the door resolves it from `<platform>`, and **rejects** a stray `-p` as conflicting |
| Preview live-facing operations before applying | `--dry-run`, plus 95 guarded verbs that refuse without `--yes` |
| Read-only validation, status and doctor are safe | those verbs are deliberately absent from the guard table — over-guarding a read is as much a bug as under-guarding a write |

Still prose, because no mechanism checks them yet:

- Never commit real env files, credentials, Terraform state, or dumps.
- Do not modify files in `docs/archive/` to describe current behavior.
- Do not add `kctl-*` source or package scaffolding here.
- Standard HTTP services use the external `dokploy-network` and Traefik.
- **Never stop or remove `dokploy` or `traefik`.** ⚠️ The door cannot refuse
  this yet — `docker restart` and `docker prune` are guarded, but nothing knows
  those two container names are special. A `PreToolUse` deny hook is the fix, the
  way `kodemeio-odoo/bin/hooks/require-18-0-branch.py` denies a commit off the
  wrong branch. Until then this is on you.
- Deploys are asynchronous; verify completion and health (`./dokploy.sh health <platform>`).
- Preserve ignored files under `deploys/env/` during repository moves.

See `README.md` and `docs/architecture.md` for the current system boundary.
