# OMP coding rollout

Desired coding integration: `tgunawandev/kodemeio-omp`, pinned OMP 18.6.0, direct
Codex OAuth (Sol/Luna) and optional DeepSeek V4.1 Flash (`deepseek-flash`).
Contracts: `contracts/coding_agents/{friday-omp,veronica-omp}.json`.

New operator coding starts through the OMP front door, with separate FRIDAY and
VERONICA sessions/worktrees. There is no new public Dokploy application to create
for native terminal use. A dedicated coding host over SSH or the optional OMP
terminal container is sufficient. Do not point the old DSH HTTP healthcheck at a
terminal process and mark it healthy.

Current contracts intentionally record credentials-pending local activation.
Legacy `contracts/agents/friday.yaml`, `kod-infra-dsh` desired state, shared
Authentik outpost and llmlite stack remain until runtime cutover is accepted.
Local source archival retains compatibility symlinks. GitHub archival/gateway
shutdown requires all consumers to be migrated and the rollback evidence saved.

Before remote cutover, follow the complete checklist in
[`kodemeio-omp/docs/migration.md`](../../../kodemeio-omp/docs/migration.md).
In particular, require authenticated model availability, contained runner/broker
separation, trusted `/run` outcomes, VERONICA's same-head gate, real target tests,
budget/usage controls, alert redaction and an end-to-end canary draft PR.

All live inventory, desired-state validation and redeployment still go through
`./dokploy.sh` (and Hermes changes through `./hermes.sh`). A service redeploy may
reuse its current image tag; independently confirm the new live image/process
before declaring cutover. The archive action does not stop a live service.
