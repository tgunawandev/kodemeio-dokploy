# OMP coding rollout

Two persistent operator terminals are deployed in `apps/production` on
`kod-prod-02` (88.198.151.167), stack `kod-infra-omp`, compose
`OaeHlGC5BvVH6R3W-rh9q`. Current capability release: `8ff7f5e`, image `kodemeio-omp:8ff7f5e`, pinned
OMP 18.6.0/Bun 1.3.14. The previous `f8ba70d` release passed native
healthchecks and direct DeepSeek V4.1 Flash Max two-read inference tests on
2026-10-04; those provider results are historical.

The 2026-10-05 YOLO/capability rollout uses tested release branch
`release/omp-local-cloud-yolo-20261005`, source
`8ff7f5ecef01f3dcb1cac5554a60ca56615abc43`, image tag `8ff7f5e`.
The initial YOLO rollout `ced6e4e` completed as deployment
`Qj55mCvefvhVKbAKKmOip`; both live processes used YOLO. The follow-up aligns
FRIDAY/VERONICA tools, project skill discovery and native browser registration.
The production manifest pins the build label and image tag to that commit;
auto-deploy stays disabled. The source change is in a draft PR pending the
founder's merge decision. This updates the operator terminals, not unattended
dispatch or any legacy consumer.

YOLO is the default for FRIDAY and VERONICA and every provider preset in the
native checkout and image. Explicit deny/forced-prompt tool policies still
apply; persona separation, provider selection and same-head acceptance gates
are preserved. For one native chat, `omp --approval-mode write` or `always-ask`
requests prompts. Terminal records now include `approval_mode`; verify that
field and the actual Bun command arguments after each rollout. Both personas
have identical edit/write, AST, LSP, JS eval/browser and worker capabilities;
VERONICA's persona instructions define review behavior. Their state and code
volumes remain separate, and dirty source still invalidates exact-head approval.
Project skill roots load from the chosen Git root for both. LSP/browser binaries
and target app dependencies need preparation in the target development runner.

Use `runtime_sha256`, source revision, active skill digests and OMP/Bun for code
parity. Dokploy reserializes Compose YAML in its build context, so raw
`integration_sha256` can differ solely due to formatting; compare parsed Compose
settings separately. Exact OS-image parity requires one promoted registry digest.

Current coding profiles use `contracts/coding_agents/profile.v2.schema.json`,
with explicit model/effort and schema version 2. Legacy v1 profiles remain
compatible without required effort fields.

FRIDAY develops; VERONICA independently plans, verifies and reviews. Each has
separate state/code volumes, non-root UID 10001, a read-only root, no published
ports, no Docker socket and a private coding network. The 768 MiB/one CPU limit
per persona fits current operator capacity; large builds and multiple workers
need a measured capacity review. Direct API calls are explicit and paid.

## Verified capability rollout — 2026-10-05

Deployment `9ocy_5g8CmzMECeRt6hll` completed with status `done`. Both cloud and both
local containers are healthy at source `8ff7f5e`; their actual Bun processes
report YOLO and the same 14 interactive tool names. Managed capability/worker
settings match between personas. Native/local/cloud runtime fingerprint:
`ba0145b4215e3671aa44681b0ce9faa9efbf7ee2045f7829a670eaf7781b5312`.
Packaged skill hashes and OMP/Bun match; cloud/local Compose settings parse
identically despite Dokploy formatting. Cloud keeps DeepSeek; local Docker
keeps Codex. No new provider inference or browser/LSP handshake was performed.

Validation passed 31 OMP tests, 12 actual upstream tool plans, project skill URI
reads and browser-prelude registrations, native Python/TypeScript AST fixtures,
and GitHub CI run `37255362454`. OMP draft PR:
https://github.com/tgunawandev/kodemeio-omp/pull/1 (no automatic merge).
Sanitized observations: [capability rollout evidence](../evidence/omp-capabilities-2026-10-05.json).
The desired-state repository's 1288 Python tests (2 skipped) and Ruff checks
passed. Full `just check` could not run locally because just/Terraform are
absent; no Terraform files were changed. Manifest validation and both preview
steps passed before the operator front-door rollout.

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
key lives in ignored `.env.kod-infra-omp`, mode 0600; never print its content.
Build/image revision and provider/resource selection are committed env overrides. The GitHub provider reads the private OMP repository.
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
both original workspace entries are removed. Legacy source checks resolve
these archives directly, while CI keeps its explicit sibling checkout. Existing gateway,
DSH runner and shared design/DSH Authentik outpost were not stopped. VISION, KIDO
and factories require migration before gateway shutdown or read-only GitHub
archival. No legacy superuser/Odoo credentials are forwarded into OMP.

Detailed evidence and gates: [OMP migration](https://github.com/tgunawandev/kodemeio-omp/blob/main/docs/migration.md)
and [live deployment record](https://github.com/tgunawandev/kodemeio-omp/blob/main/docs/deployment-2026-10-04.json).
