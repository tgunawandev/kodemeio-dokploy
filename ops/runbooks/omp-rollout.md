# Initial OMP operator-terminal rollout (historical)

The active manifest targets `kod-ovh-01`; its fresh migration is deployed.
See [the active migration record](omp-ovh-migration.md) for current 3 GiB/2 CPU
limits, successful bounded provider reads and unrecovered old state.
The following records describe the initial 2026-10-05 rollout, not current
proof that the old host is reachable.

The initial rollout used `deploys/instances/production/kod-infra-omp.yaml` for
Dokploy compose `OaeHlGC5BvVH6R3W-rh9q` in `apps/production` on `kod-prod-02`
(88.198.151.167). Preserve its app name `compose-program-optical-panel-vswhbh`:
that name owns the four existing state/workspace volumes.

The agent-boundary release pins source
`6779db2e6be05d01d04e00c6b4e3db59f0c21239` (OMP `main`, PR #2) and the tested
registry image
`ghcr.io/tgunawandev/kodemeio-omp@sha256:aac0a855a0e95efb0054d29bef89d01f3cf5e1df0f6f79d3bdddaf6a80ea5163`.
Its config/image ID is
`sha256:d059deccec05fd8dbead041e4f2b0b292cc05e3124b8e9de410f0b00a7180cc2`.
The registry package is private. The release branch must resolve to this source
SHA before execution; a moved branch invalidates the rollout evidence.

## Initial rollout roles, access and capacity (historical)

FRIDAY implements software changes in isolated task worktrees. VERONICA owns
application diagnostics and independent release review, with read-only source
tools. Their enforced tool sets differ. YOLO remains the native default but
cannot override the role/path/secret guards or grant release approval.
JARVIS owns business/data and SENTINEL owns infrastructure in Hermes; this
terminal update does not migrate their live bindings. Other personas retain
existing authority.

At the initial old-host rollout, both terminals kept separate state/code volumes, UID 10001, a read-only root,
no public ports, no Docker socket, no added host/SSH/GitHub/deployment credentials,
one CPU and 768 MiB each. They retain DeepSeek and the existing provider secret.
Native Codex login remains separate per persona; never copy OAuth state.
At that initial rollout, live provider acceptance was incomplete at its resource limits.
Post-deployment verification started a second full OMP process inside these limits; the kernel recorded a
Bun OOM kill. FRIDAY restarted twice during those checks. Later input checks
also coincided with one restart per terminal; their cause remains under
investigation. Process health alone does not establish prompt readiness.
Both isolated
synthetic prompt/read checks passed at 1 GiB after skipping the optional
provider setup. FRIDAY reached the 1 GiB limit; VERONICA peaked at 957,435,904
bytes. Initial old-host prompt acceptance was unresolved at 768 MiB. The following
capacity warning applied to that old host, not the completed OVH move: do not
raise both limits on this shared 4 GiB host without a capacity plan. A dedicated
runner or additional host RAM (starting with at least 8 GiB) needs explicit
provisioning authorization and paired workload validation; no server was
provisioned or resized during this rollout.

Use only the existing native terminal for production provider checks. Run the full `./omp.sh doctor` and workflow smoke in a disposable canary
with its own resources; use lightweight Python fingerprints and process
inspection on live terminals. Concurrent runtimes and large target builds need
a tested capacity increase before enabling those workloads.

Orca, Hermes Control, the remote Desk writer and the automatic production
gateway adapter remain unavailable/disabled. The source ships a tested shared
authorization/release library, not a registered unattended deployment service.
This is a human-authorized operator rollout through the existing Dokploy CD
pipeline after GitHub CI and a recorded native VERONICA review. Do not represent
the operator's credentialed session as an authenticated CI/agent principal or
enable missing integration flags to make a gate appear satisfied.

## Preparation and deployment

Historical old-host procedure. For the active OVH compose, use
[omp-ovh-migration.md](omp-ovh-migration.md) and `ops/scripts/omp_ovh_guard.py`;
the old compose below is missing and the legacy guard rejects the OVH manifest.


1. Freeze source and digest. Confirm OMP `main` is protected, required GitHub
   Actions `validate` succeeded at that SHA, and the independent source review
   still applies. Review the deployment configuration/evidence at their exact
   head. Require explicit founder production approval for this artifact/target;
   approval does not authorize future automatic releases.
2. Build once, publish the tested image, confirm registry config digest matches
   the tested image ID and package visibility stays private. Never rebuild on
   production. The manifest's custom Compose command uses `--no-build` and
   `--pull never`, so the cloud requires the approved preloaded digest.
   Preload it through the trusted host image client with a short-lived,
   repository-scoped pull bearer; never forward a founder token or persist
   registry credentials in an agent container. Verify its RepoDigest and image
   ID before deployment. A missing image fails closed. The active and rollback source branches are
   locked with administrator enforcement; also recheck their exact SHA.
3. Read existing compose identity, environment/server, provider, secret values
   and four mounts privately. Compare the ignored env file with the deployed
   secret before setup; stop on unexplained drift. Never print either secret,
   pass it into a model, or replace it from an unverified source.
4. Back up compose configuration and all four volumes in private operator
   storage. Briefly pause only these terminals while archiving, always unpause
   in a `finally` path, then test extraction and file digests in new temporary
   directories. Never restore over live volumes. Preserve the previous image
   and authorization/routing audit history.
5. Run a bounded disposable canary with the exact image: non-root, read-only,
   no production mounts/credentials, dropped capabilities and resource limits.
   Test FRIDAY → operator regression checks → VERONICA review with a synthetic
   fixture and the existing provider credential. Attribute infrastructure
   evidence to the operator until the SENTINEL adapter exists.
6. Validate and preview through the owning front door, then execute only after
   rechecking frozen SHA/digest/review/CI/target and approval. Keep auto-deploy
   disabled and preserve the existing compose ID/app name. After setup, require
   trusted API readback through `ops/scripts/omp_rollout_guard.py` before
   triggering deployment; the CLI applies advanced settings on a best-effort
   basis, so successful setup alone does not prove the command was accepted.

```bash
./dokploy.sh kodemeio deploy validate -f deploys/instances/production/kod-infra-omp.yaml
./dokploy.sh kodemeio deploy setup -f deploys/instances/production/kod-infra-omp.yaml --kctl-dry-run --yes
./dokploy.sh kodemeio deploy run -f deploys/instances/production/kod-infra-omp.yaml --skip-verify --kctl-dry-run --yes
./dokploy.sh kodemeio deploy setup -f deploys/instances/production/kod-infra-omp.yaml --yes
# Client 0.18.5 setup did not persist this advanced field; apply it explicitly.
./dokploy.sh kodemeio compose update --id OaeHlGC5BvVH6R3W-rh9q --command 'compose -p compose-program-optical-panel-vswhbh -f docker-compose.prod.yml up -d --no-build --pull never --remove-orphans' --yes
# BEFORE compose start: read compose get privately and call omp_rollout_guard.assert_staged.
# Verify command, exact reviewed source/image, target, project, createEnvFile,
# auto-deploy and preserved environment; independently resolve the locked Git ref.
./dokploy.sh kodemeio compose start OaeHlGC5BvVH6R3W-rh9q --yes
```

The actual 0.18.5 setup advanced-settings path failed to resolve its client and
silently skipped the custom command. The readback guard caught the empty field
and prevented restart. The explicit typed `compose update` above corrected that
field; record and verify the response before proceeding. The mocked transport
check proved the method can forward the field, not that live setup would do so.
A client fix belongs in the CLI owning repository.

After setup, explicit command update and readback, `compose start` queues
deployment of the verified record without another best-effort configuration phase. The generic HTTP
verifier cannot verify terminals. The `deploy run` preview uses `--skip-verify`
to skip only
that inapplicable check; native health, image/process/tool checks and bounded
provider acceptance remain required. A passing isolated canary does not replace
production prompt acceptance at the actual production limits.
Deployment is asynchronous: a queued API response is not success. Poll the deployment to `done`, then inspect both Docker
healthchecks, exact image/config/source revision, native Bun arguments, role/tool
sets, mounts and privilege/network boundaries. Record VERONICA's post-deployment
assessment from bounded sanitized evidence. A process-health result must not
be reported as complete application acceptance when a prompt check failed.
Escalate missing/failing checks;
no automatic rollback or subsequent release is authorized.

## State and rollback

The 2026-10-05 preparation backup is private at
`/root/omp-release-backups/6779db2-20261005` on `kod-prod-02` (mode 0700).
Four archives were restored into scratch directories and every regular-file
digest checked. State/transcripts are sensitive: never commit these archives or
the API/environment snapshot. This one-time local backup is not proof of
scheduled/off-site retention or disaster recovery.

Previous source: `8ff7f5ecef01f3dcb1cac5554a60ca56615abc43`, branch
`release/omp-rollback-8ff7f5e`, image `kodemeio-omp:8ff7f5e`, image ID
`sha256:37fe34650226e84a714f8446c9ed2673bce8ad312f29cfc845c9dfe9fc3eeb22`.
Retain that image. Rollback requires founder authorization for the exact target
and prior artifact. Prefer restoring the previous image/config with existing
volumes; never blindly overwrite state or revive consumed approvals. Check
compatibility/native health again. Audit recovery must preserve newer rows and
reconcile pending/claimed work.

## Operator access and retained consumers

From `kodemeio-omp`, use `./scripts/remote-terminal.sh friday` or `veronica`;
detach with Ctrl-b then d. Code-only imports use allowlisted
`scripts/provision-code.py` preview/apply, without founder credentials. Target
framework dependencies still need preparation in their code runners.

Previous capability deployment `9ocy_5g8CmzMECeRt6hll` at `8ff7f5e` is historical;
its identical-tool results do not describe agent-boundary's role boundaries. Source
PR #2 and CI run `37289978526` validate the new coding harness.
Production deployment `3-l4_f-kZt6b6XpIDrHYD` completed on 2026-10-05.
See [cutover evidence](../evidence/omp-production-cutover-2026-10-05.json) for
verified live fingerprints/tools, provider acceptance status and limitations.
These results cover the two operator terminals and the isolated coding flow;
they do not establish the missing cross-system agent integrations.

This remains an operator terminal deployment, not Hatchet's authenticated
`/run` service. Preserve legacy FRIDAY broker/idempotency/contained-runner,
gateway, DSH runner and shared Authentik outpost until their exact cutovers are
proved. DSH/llmlite source clones remain in sibling `kodemeio-archived`; their
live consumers were not stopped. Hatchet, Prometheus, Tempo,
Grafana/Alloy/OpenTelemetry and 1Password keep their existing responsibilities.
See the OMP [migration record](https://github.com/tgunawandev/kodemeio-omp/blob/main/docs/migration.md).

The front-door guard table includes context-specific `deploy setup`, `run` and
`post` writes in addition to the generic derivation. These verbs require
`--yes` before the real client runs; mock-CLI refusal/forwarding tests cover
all three. CLI dry-run previews also carry the door confirmation, while the
actual `--dry-run` forwarded to the CLI prevents effects. Read-only validation
and compose lookup remain available.

Managed JSON-in-YAML remains intentional. The legacy backup script's textual
column-zero scan does not recognize this style; this terminal manifest must
not inherit a database backup block. Tests assert no `extends`/`backup`, and
operator volume backup/restore evidence is recorded separately. Scheduled and
off-site retention remain a configuration gap.
