---
name: dokploy-deployment
description: >
  Operating live dokploy targets through ./dokploy.sh — desired state, infrastructure,
  monitoring and runbooks. CLI implementation lives in kodemeio-skills, not here. Use when the
  user asks to deploy, restart, roll back, back up or restore, inspect logs or health, or
  change anything on a running dokploy instance. NOT for editing this repository's own code,
  manifests or tests — that is dokploy-development.
---

# dokploy-deployment

Read first: [_shared/references/cli-profiles.md](../_shared/references/cli-profiles.md) — the door contract, the targets and where the guards are missing.

## Targets

| platform | profile | | writes |
|---|---|---|---|
| `kodemeio` | `kodemeio` | Kodemeio platform | **guarded** |
| `idtpp` | `idtpp` | IDTPP platform | **guarded** |
| `abcfood` | `abcfood` | ABCFood platform | **guarded** |
| `local` | `local` | Local Dokploy | free |

`local` is the throwaway; guards do not apply there.

## The door — one way in

```bash
./dokploy.sh <platform> <group> <verb> [args]   # resolved + guarded
./dokploy.sh <reserved> [args]              # a repo tool from bin/dokploy/
./dokploy.sh <anything else>                # forwarded to kctl-dokploy verbatim
```

Run `./dokploy.sh` with no arguments for usage. It is generated from `dokploy.yaml` and from `bin/dokploy/` on disk, so it cannot drift from what the door actually does — never from a list written in prose.

**`--dry-run` prints the resolved command and runs nothing.** It is the door's own flag, not the CLI's, so it is safe against any target and is the cheapest way to see what a line will really do.

🔴 **`--yes` is the DOOR's confirmation, not the CLI's.** The door reads it, then removes it before forwarding — because almost no verb in this fleet owns a `--yes` of its own, and passing one would make the CLI exit on `No such option`. Where a verb genuinely does own one it is listed in `forward_yes:` and passed through; `--kctl-yes` forces that by hand.

## Guarded verbs — 95 of them, they need `--yes`

Derived from `kctl-dokploy commands tree`, not hand-listed. Re-derive after a CLI
upgrade and the change shows up as a diff:

```bash
kodemeio-skills/scripts/frontdoor-guards verify dokploy.yaml
```

| group | verbs |
|---|---|
| `applications` | `cancel`, `create`, `delete`, `deploy`, `move`, `redeploy`, `start`, `stop`, `update` |
| `certificates` | `create`, `delete`, `import` |
| `compose` | `autodeploy disable`, `autodeploy enable`, `backups create`, `backups delete`, `backups restore`, `backups rollback`, `backups update`, `cancel`, `create`, `delete`, `deployments cancel`, `deployments delete`, `deployments kill`, `deployments redeploy`, `domains create`, `domains delete`, `domains update`, `env delete`, `env push`, `env set`, `import`, `mounts create`, `mounts delete`, `mounts update`, `move`, `patches create`, `patches delete`, `patches mark-delete`, `patches toggle`, `patches update`, `ports create`, `ports delete`, `ports update`, `redeploy`, `redirects create`, `redirects delete`, `redirects update`, `schedules create`, `schedules delete`, `schedules update`, `security create`, `security delete`, `security update`, `start`, `stop`, `update`, `volume-backups create`, `volume-backups delete`, `volume-backups restore`, `volume-backups update` |
| `databases` | `delete`, `deploy`, `stop` |
| `deploy` | `apply`, `migrate apply`, `migrate rollback`, `rollback` |
| `docker` | `prune`, `restart` |
| `git` | `create`, `delete`, `update` |
| `notifications` | `create`, `delete`, `update` |
| `projects` | `create`, `delete`, `environments create`, `environments delete`, `environments update`, `update` |
| `registry` | `create`, `delete`, `update` |
| `servers` | `create`, `delete`, `update` |
| `settings` | `update` |
| `template` | `apply`, `delete` |
| `users` | `create`, `delete`, `update` |

Anything not on this list runs without `--yes`. Read verbs are absent on
purpose: over-guarding a read is as much a bug as under-guarding a write.


## CLI groups this skill owns

`compose`(109) · `servers`(28) · `deploy`(23) · `applications`(16) · `projects`(12) · `databases`(12) · `docker`(9) · `git`(8) · `notifications`(7) · `template`(7) · `registry`(6) · `users`(6) · `certificates`(6) · `settings`(6) · `diagnose`(6) · `report`(5) · `audit`(4) · `setup`(2) · `dashboard`(1) · `doctor`(1)

## Health and status — the read-only repo tools

```bash
./dokploy.sh check idtpp                 # every service: status AND backup, with last backup date + size
./dokploy.sh check idtpp --problems      # only what is not done or not proven backed up
./dokploy.sh check all --no-backup       # fleet-wide service status in seconds
./dokploy.sh services idtpp --problems   # status only; `status` is the real field, never `composeStatus`
./dokploy.sh backup idtpp                # backup CONFIG joined to the OBJECTS in S3, plus manifest defects
./dokploy.sh hosts idtpp                 # CPU / memory / disk per server, over SSH
./dokploy.sh health kodemeio             # HTTP reachability plus the CLI's own checks
```

Reach for `check` first: it is a join of `services --json` and `backup --json`
and answers "is it up, and is it backed up?" on one screen. All of these only
read, so none takes `--yes` or `--dry-run`. `check all` and `backup` are slow
(about 2s per service, abcfood ≈ 4 min); `--no-backup` is the fast path.

## Discovery — use this instead of guessing

```bash
kctl-dokploy commands list --filter <keyword>   # narrow, cheap
kctl-dokploy commands tree --json               # every group and leaf, with flags
./dokploy.sh help <group>              # the group's own --help
```

🔴 `commands tree` prints a `WARN Update available:` banner to **stdout** ahead of
the JSON on several of these CLIs, which makes the output unparseable as-is.
Strip everything before the first `{`.

## Routing

| The question | Skill |
|---|---|
| Editing this repo — code, manifests, tests, the door itself | `dokploy-development` |
| The Odoo fleet | `odoo-deployment` / `odoo-development` in `kodemeio-odoo` |
| The CLI's own implementation | `kodemeio-skills/packages/kctl-dokploy/` |

---

🔴 **This skill is GENERATED and NOT YET VERIFIED.** Everything above is measured — targets from the profile config, guards from the CLI's own command tree, the door contract from the vendored dispatcher. What is missing is the part a generator cannot produce: the **symptom → route** table, and the traps that have actually cost someone a day. Add those, check the facts above against the live service, then set `verified` in `skill.toml`. Until then `kctl-skill lint` reports it stale, which is correct.
