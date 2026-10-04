# OMP coding rollout

Two persistent operator terminals are deployed in `apps/production` on
`kod-prod-02` (88.198.151.167), stack `kod-infra-omp`, compose
`OaeHlGC5BvVH6R3W-rh9q`. Deployed source: `f8ba70d`, image
`kodemeio-omp:f8ba70d`, pinned OMP 18.6.0/Bun 1.3.14. Both native healthchecks
and direct DeepSeek V4.1 Flash Max two-read inference tests passed on 2026-10-04.

Current coding profiles use `contracts/coding_agents/profile.v2.schema.json`,
with explicit model/effort and schema version 2. Legacy v1 profiles remain
compatible without required effort fields.

FRIDAY develops; VERONICA independently plans, verifies and reviews. Each has
separate state/code volumes, non-root UID 10001, a read-only root, no published
ports, no Docker socket and a private coding network. The 768 MiB/one CPU limit
per persona fits current operator capacity; large builds and multiple workers
need a measured capacity review. Direct API calls are explicit and paid.

## Access and code preparation

From `kodemeio-omp`, run `./scripts/remote-terminal.sh friday` or the same command
for VERONICA. Detach with Ctrl-b then d. Code-only published clones can be
imported by `scripts/provision-code.py --repo <allowlisted-name>` (preview), then
`--apply`. Odoo uses its published `18.0` branch. The helper includes Git history
but excludes ignored local envs and never gives agents GitHub/SSH credentials.
Target framework dependencies must still be installed in the code workspace.

The deployed preset is `deepseek`, using the canonical `deepseek-flash` V4.1 ID.
VERONICA is configured for GPT Sol 6.1 High; FRIDAY for GPT Luna 6 Max.
The founder confirmed Luna 6 Max after checking that Luna 6.1 is absent from the pinned catalog and the current official
model list; no alias is invented. DeepSeek uses OMP xhigh, mapped to API max.
Codex models remain configured but require native OAuth separately for each
persona. Use the remote helper's `login` and `models` actions and verify both
models before changing the preset. `OMP_FRIDAY_PRESET` and `OMP_VERONICA_PRESET`
override the shared preset independently after each account is verified. Profiles explicitly record
`remote-deepseek-codex-pending`; no paid fallback is automatic.

## Deployment and verification

The desired state is `deploys/instances/production/kod-infra-omp.yaml`. The API
key and build/image revision live in ignored `.env.kod-infra-omp`, mode 0600;
never print its content. The GitHub provider reads the private OMP repository.
Auto-deploy remains off: code/documentation pushes alone do not restart terminals.

```bash
./dokploy.sh kodemeio deploy validate -f deploys/instances/production/kod-infra-omp.yaml
./dokploy.sh kodemeio deploy setup -f deploys/instances/production/kod-infra-omp.yaml --kctl-dry-run
./dokploy.sh kodemeio deploy setup -f deploys/instances/production/kod-infra-omp.yaml --yes
./dokploy.sh kodemeio deploy run -f deploys/instances/production/kod-infra-omp.yaml --skip-verify --yes
```

The generic verifier requires HTTP and cannot validate these terminals. Instead
inspect both native Docker healthchecks, actual image revision/process and the
mounted/network/privilege boundaries over host SSH. Run explicit small provider
smoke tests inside each persona container when credentials/runtime have changed.
The initial command exhausted its default ten-second deployment wait; the
asynchronous build completed and was verified independently. The manifest now
allows 300 seconds. A queued deployment is never a live-success claim.

State and transcripts are secrets. Configure backups/retention and prove restore
before unattended operation. Do not treat idle terminal health as evidence that
all target applications can build within this host's memory limits.

## Remaining production cutovers

This is an operator terminal deployment, not Hatchet's authenticated `/run`
service. Preserve legacy FRIDAY broker/idempotency/contained-runner contracts
until the OMP adapter and independent review canary pass. GitHub publication,
merge and deployment remain operator/broker responsibilities.

DSH and llmlite local clones are now under the sibling `kodemeio-archived` folder;
workspace compatibility symlinks preserve historical readers. Existing gateway,
DSH runner and shared design/DSH Authentik outpost were not stopped. VISION, KIDO
and factories require migration before gateway shutdown or read-only GitHub
archival. No legacy superuser/Odoo credentials are forwarded into OMP.

Detailed evidence and gates: [OMP migration](https://github.com/tgunawandev/kodemeio-omp/blob/main/docs/migration.md)
and [live deployment record](https://github.com/tgunawandev/kodemeio-omp/blob/main/docs/deployment-2026-10-04.json).
