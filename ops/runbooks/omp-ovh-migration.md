# OMP migration to kod-ovh-01

Status: deployed fresh on 2026-10-05 after the founder instructed "go ahead,
deploy it. merge the PR." following the backup/fresh-start explanation.
[Cutover evidence](../evidence/omp-ovh-cutover-2026-10-05.json) records the merged
configuration, completed deployment, exact artifact and bounded live reads.
Old workspaces/state/history remain unrecovered; no legacy resource was deleted.
Both terminals were healthy with zero restarts/OOM events. The initial
[preparation receipt](../evidence/omp-ovh-migration-plan-2026-10-05.json) remains
historical and is not rewritten into a deployment-success claim.

The destination is registered in the Kodemeio Dokploy instance as
`W-sYqWjxLu9cAhp41pwpA`, `kod-ovh-01`, `57.131.162.248`. Capacity was measured as
11,956,708 KiB RAM, 6 CPUs and 99,720,445,952 bytes free in Docker storage.
FRIDAY and VERONICA each receive 3 GiB RAM and 2 CPUs. Public ports remain absent;
non-root, read-only root, separate volumes and dropped capabilities remain.
There are no new host, GitHub, deployment or business credentials in the agents.

The move preserves source `6779db2e6be05d01d04e00c6b4e3db59f0c21239` and private
image `ghcr.io/tgunawandev/kodemeio-omp@sha256:aac0a855a0e95efb0054d29bef89d01f3cf5e1df0f6f79d3bdddaf6a80ea5163`.
The target image/config ID is
`sha256:d059deccec05fd8dbead041e4f2b0b292cc05e3124b8e9de410f0b00a7180cc2`.
The image is already preloaded and verified on the destination using a
short-lived repository-scoped pull bearer. No founder token was forwarded and
no registry credential was persisted. The isolated cloud workflow passed on
the destination at the proposed limits: FRIDAY fix/commit → regression checks
→ VERONICA independent review. It used no production mounts or business data.

## Source state and new identity

The original compose `OaeHlGC5BvVH6R3W-rh9q` returns 404, and its original server
entry no longer exists. `88.198.151.167` failed SSH banner exchange directly and
via an OVH jump host; TCP22 also timed out from OVH. These records were removed
outside this migration; do not claim this task deleted them or that their
volumes were recovered. The last protected operator API snapshot retains the
provider environment. Its key still worked in the synthetic cloud canary.

The old four-volume backup was on
`/root/omp-release-backups/6779db2-20261005` on that unreachable host. Previous
scratch restore evidence remains historical; actual archive bytes are not
available locally. There is no proved cross-host/off-site recovery source.
Existing audit and review receipts stay intact. No original volumes, shared
services or legacy runner consumers were stopped or deleted by this task.

Replacement compose: `QNgRohOENxsbnmFdGhJoC`, apps/production environment
`k0aSx0yqY5o3brJYjtaw3`. Its new Dokploy app name is
`compose-override-online-protocol-4e2bjl`. The explicit Compose `-p` argument
remains `compose-program-optical-panel-vswhbh`, preserving the known four-volume
namespace on the new host. Metadata app name and Docker volume namespace are
separate here; never substitute the generated new app name into that command.

## Cutover procedure (completed for the recorded fresh start)

1. Record the founder's data choice privately. **Restore** requires readable
   archives, digest checks, safe extraction into scratch, then restoration to
   the four separate destination volumes with UID/GID 10001. Preserve each
   persona's own state and audit history. Do not cross-copy credentials between
   personas. **Fresh** requires an explicit founder selection, a record that
   old state/workspaces were not recovered, and verification that no unrelated
   destination volume or container will be overwritten. A pending choice blocks
   both paths. A model's decision cannot substitute for this human selection.
2. Recheck the target's available RAM/disk, exact image ID/RepoDigest/source
   label, locked source ref, required source CI, immutable configuration head,
   completed owning CI and independent native VERONICA configuration review.
   Preserve the unrelated uncommitted OMP and deployment-repository changes;
   they are excluded from this immutable image move.
3. Validate and preview the desired manifest through `./dokploy.sh kodemeio`.
   Setup only stages configuration. Apply the explicit typed custom command to
   work around the documented 0.18.5 advanced-settings propagation problem.
   The explicit update is:

   ```bash
   ./dokploy.sh kodemeio compose update QNgRohOENxsbnmFdGhJoC \
     --command 'compose -p compose-program-optical-panel-vswhbh -f docker-compose.prod.yml up -d --no-build --pull never --remove-orphans' \
     --no-auto-deploy --yes
   ```

   Fetch the new compose privately and call `omp_ovh_guard.assert_staged` with
   the saved operator source snapshot. It must pass after staging and again
   immediately before queueing deployment. `omp_rollout_guard` is a historical
   old-host guard; it now rejects a manifest naming a different host.
4. Keep auto-deploy disabled. Record a one-time operator execution claim bound
   to the current head, image, new compose/server/environment, exact staged
   parameters and human data selection before `compose start`. No registered
   automatic CI/agent release adapter exists; never impersonate a CI principal.
   Queue only the new compose and poll the asynchronous result to `done`.
5. Check both live processes, image/runtime/skills fingerprints, actual guarded
   tool/role arguments, volume ownership, privilege boundaries, healthchecks
   and restart counts. Complete optional first-run setup without changing
   provider access, then perform one bounded synthetic read through each
   existing terminal. No production data, messages or unrestricted logs go
   into model context. Record independent VERONICA health review from sanitized
   evidence. Paired target workloads and heavy builds require their own tests.

The owning front door is the only Dokploy API path. All confirmed writes use
`--yes`; previews use `--kctl-dry-run` where supported. Do not call stop/start on
Dokploy or Traefik. Do not run a second full CLI for routine health checks inside
a live terminal. Use lightweight Python fingerprints/process inspection;
full doctor/workflow tests belong in disposable resource-isolated containers.

## Access and recovery

After successful cutover, use:

```bash
OMP_SSH_HOST=root@57.131.162.248 ./scripts/remote-terminal.sh friday
OMP_SSH_HOST=root@57.131.162.248 ./scripts/remote-terminal.sh veronica
```

These explicit overrides are needed because the immutable release's local
access helper defaults to the old host. The source checkout has unrelated
uncommitted changes and is not modified or published by this move. Do not use
that old default as proof the old service remains reachable.

If acceptance fails, stop only the new OMP terminals through the owning front
door and retain their state/audit evidence. Reverting a manifest to the old
host is not an executable rollback while that host and compose are missing.
Recovery needs a reachable approved host and verified state backup, or the
founder's explicit fresh-start selection. No automatic rollback is enabled.
Before deleting any live resources, preserve backups and obtain authorization
for that specific removal. The previous source/image/audit records remain
historical and are not overwritten with new success claims.

Orca, Hermes Control, Desk ticket-writing, Hermes authorization adapters and
the automatic production release gateway remain unregistered/disabled. This
move does not activate them or migrate JARVIS/SENTINEL. Hatchet, Prometheus,
Tempo, Grafana/Alloy/OpenTelemetry and 1Password keep their existing roles.
The OVH host's provider-level automated backup is not verified per-volume
application restore or an off-site OMP backup schedule.
