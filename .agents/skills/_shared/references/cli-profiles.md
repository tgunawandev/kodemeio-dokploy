# The door contract — targets, guards, discovery

Shared by `dokploy-development` and `dokploy-deployment`. Everything here is
generated from `dokploy.yaml` and the CLI's own command tree; when it disagrees
with the repo, the repo is right and this file is stale.

## Contents

- One front door
- Targets and profiles
- Guards — and what `--yes` actually means
- Where the guards are missing

## One front door

```bash
./dokploy.sh <platform> <group> <verb> [args]   # resolved + guarded
./dokploy.sh <reserved> [args]              # a repo tool from bin/dokploy/
./dokploy.sh <anything else>                # forwarded to kctl-dokploy verbatim
```

Run `./dokploy.sh` with no arguments for usage. It is generated from `dokploy.yaml` and from `bin/dokploy/` on disk, so it cannot drift from what the door actually does — never from a list written in prose.

**`--dry-run` prints the resolved command and runs nothing.** It is the door's own flag, not the CLI's, so it is safe against any target and is the cheapest way to see what a line will really do.

🔴 **`--yes` is the DOOR's confirmation, not the CLI's.** The door reads it, then removes it before forwarding — because almost no verb in this fleet owns a `--yes` of its own, and passing one would make the CLI exit on `No such option`. Where a verb genuinely does own one it is listed in `forward_yes:` and passed through; `--kctl-yes` forces that by hand.

## Targets and profiles

| platform | profile | | writes |
|---|---|---|---|
| `kodemeio` | `kodemeio` | Kodemeio platform | **guarded** |
| `idtpp` | `idtpp` | IDTPP platform | **guarded** |
| `abcfood` | `abcfood` | ABCFood platform | **guarded** |
| `local` | `local` | Local Dokploy | free |

`local` is the throwaway; guards do not apply there.

## Guards — and what `--yes` actually means

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

## Where the guards are missing

The guard table is derived from verb NAMES matched against a write-verb list. It
is a good filter, not an oracle. Two known classes of gap:

1. **A write verb with an unusual name** is not matched and so not guarded. Run
   `frontdoor-guards verify dokploy.yaml` — its `UNGUARDED` warnings are exactly
   this list.
2. **The CLI can be reached without the door.** `kctl-dokploy -p <profile> …` works and
   is guarded by nothing. The door is a habit, not a fence; the only real fence
   would be a per-profile `readonly` flag, and no profile sets one.
