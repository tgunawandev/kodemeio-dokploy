# Odoo domain rename (`kctl-dokploy domains move`)

Moves a service to a new public hostname while every old hostname keeps working as a redirect
and Authentik OIDC keeps working.

- Design: kodemeio-docs/superpowers/specs/2026-09-13-idtpp-odoo-domain-rename-design.md
- Rollout: kodemeio-docs/superpowers/plans/2026-09-13-idtpp-domain-rename-b-rollout.md
- Desired state: deploys/domains/idtpp.yaml

## One stage for one instance

1. Change that move's `stage:` in `deploys/domains/idtpp.yaml`; commit.
2. `kctl-dokploy -p idtpp compose domains move plan -f deploys/domains/idtpp.yaml -i <instance>` — read the actions, note the digest.
3. `./dokploy.sh idtpp compose domains move apply -f deploys/domains/idtpp.yaml -i <instance> --yes` (prompts per action),
   or unattended with `--assume-yes --expect <digest>`; `--redeploy` only where a restart is acceptable.
4. `kctl-dokploy -p idtpp compose domains move check -f deploys/domains/idtpp.yaml -i <instance>`.
5. Regenerate manifests (`uv run python deploys/generate.py`) and commit them with the stage.

## Stages

planned → dual (both names served) → redirect (302/307) → permanent (301/308) → cleaned (after
`cleanup_after`: old env, domain cards and Authentik URIs removed; redirects and DNS stay forever).

## Rollback

Lower `stage:` and apply again. A lowered redirect is removed first (about a second); `planned`
undoes everything the move added except DNS.

## Traps

- `held` lines mean the redirect was withheld on purpose (new name not serving, websocket, base URL,
  Authentik URI, open POS sessions, or an undeclared env reference). Fix the cause; never force it.
- An env value that still names an old host blocks `redirect`: repoint it or declare it under `dependents`.
- `deploy apply` pushes the entire env with `--force` and redeploys; do not use it for these instances.
- Dependents that bake URLs at build time (React apps) need a redeploy after their env changes.
