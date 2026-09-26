# Operational runbooks

Only reviewed runbooks compatible with the current `kctl` command surface live
here. Older procedures are retained under `docs/archive/runbooks/` and are not
safe operating instructions.

| Runbook | Use |
|---|---|
| [incident-response.md](incident-response.md) | Initial triage, evidence collection, and escalation |
| [postgres-restore.md](postgres-restore.md) | Restore a compose-embedded PostgreSQL database |
| [mattermost-sg-migration.md](mattermost-sg-migration.md) | Reviewed Mattermost Singapore migration |
| [hetzner-disk-resize.md](hetzner-disk-resize.md) | Resize Hetzner disks and filesystems |
| [teracorp-odoo-rollout.md](teracorp-odoo-rollout.md) | Install/upgrade the MCP governance addons (fail-closed binding, kill switch, rollback) |

For the dependency graph, see
[docs/service-map.md](../../docs/service-map.md).

Every `kctl-dokploy` command must use an explicit profile. Never stop or remove
the `dokploy` or `traefik` platform containers.
