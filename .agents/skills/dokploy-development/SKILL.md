---
name: dokploy-development
description: >
  Working on the dokploy repository itself — desired state, infrastructure, monitoring and
  runbooks. CLI implementation lives in kodemeio-skills, not here. Use when the user asks to
  change, scaffold, lint, test or review code and configuration IN this repo, or to understand
  how ./dokploy.sh, dokploy.yaml and bin/dokploy/ work. NOT for acting on a live dokploy
  instance — deploying, restarting, backing up, or changing configuration on a running target
  is dokploy-deployment.
---

# dokploy-development

Read first: [_shared/references/cli-profiles.md](../_shared/references/cli-profiles.md) — the door contract, the targets and where the guards are missing.

## What lives here

This repository owns the desired state and the tooling. The CLI that acts on a live target (`kctl-dokploy`) is implemented in `kodemeio-skills/packages/`, not here — do not add CLI source to this repo.

The front door itself is three vendored files plus one per-repo data file:

| file | vendored? | what it holds |
|---|---|---|
| `dokploy.sh` | yes | the three dispatch rules |
| `bin/dokploy/_boot.sh` | yes | repo root, inventory lookups, colour, `need` |
| `bin/dokploy/_dispatch.sh` | yes | guards, globals, `--dry-run` |
| `dokploy.yaml` | **no** | targets, profiles, guard table — the only per-repo file |

🔴 **Never edit a vendored file in place.** Edit `kodemeio-skills/templates/frontdoor/` and run `scripts/frontdoor-sync --write`. `--check` fails the build when a copy has drifted, which is the whole reason ten repos behave identically.

## The door — one way in

```bash
./dokploy.sh <platform> <group> <verb> [args]   # resolved + guarded
./dokploy.sh <reserved> [args]              # a repo tool from bin/dokploy/
./dokploy.sh <anything else>                # forwarded to kctl-dokploy verbatim
```

Run `./dokploy.sh` with no arguments for usage. It is generated from `dokploy.yaml` and from `bin/dokploy/` on disk, so it cannot drift from what the door actually does — never from a list written in prose.

**`--dry-run` prints the resolved command and runs nothing.** It is the door's own flag, not the CLI's, so it is safe against any target and is the cheapest way to see what a line will really do.

🔴 **`--yes` is the DOOR's confirmation, not the CLI's.** The door reads it, then removes it before forwarding — because almost no verb in this fleet owns a `--yes` of its own, and passing one would make the CLI exit on `No such option`. Where a verb genuinely does own one it is listed in `forward_yes:` and passed through; `--kctl-yes` forces that by hand.


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
| Deploying, restarting, backing up, or anything on a LIVE target | `dokploy-deployment` |
| The Odoo fleet | `odoo-deployment` / `odoo-development` in `kodemeio-odoo` |
| The CLI's own implementation | `kodemeio-skills/packages/kctl-dokploy/` |

---

🔴 **This skill is GENERATED and NOT YET VERIFIED.** Everything above is measured — targets from the profile config, guards from the CLI's own command tree, the door contract from the vendored dispatcher. What is missing is the part a generator cannot produce: the **symptom → route** table, and the traps that have actually cost someone a day. Add those, check the facts above against the live service, then set `verified` in `skill.toml`. Until then `kctl-skill lint` reports it stale, which is correct.
