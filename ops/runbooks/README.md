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
| [teracorp-cw1-host.md](teracorp-cw1-host.md) | CW1: provision the Chatwoot/kido_chat host (M1) |
| [teracorp-cw1-meta-whatsapp.md](teracorp-cw1-meta-whatsapp.md) | CW1: Meta Business Manager, WABA, number, template (M2/M3) |
| [teracorp-cw1-privacy.md](teracorp-cw1-privacy.md) | CW1: privacy/consent/retention draft for counsel (M5) |
| [teracorp-cw1-rollout.md](teracorp-cw1-rollout.md) | CW1: deploy order, kill switches, handback, rollback (M6/M7) |
| [teracorp-wave0-rollout.md](teracorp-wave0-rollout.md) | Founder-gated Wave 0 rollout (G1–G10): B2 offsite, Healthchecks, Gatus, admin gates, drills |
| [kod-offsite-backup.md](kod-offsite-backup.md) | kodeme.io backups: what is copied where, and restore when Hetzner is down |
| [restore-drill.md](restore-drill.md) | Timed, isolated Odoo + Authentik restore drill from B2; record RPO/RTO |
| [supabase-export.md](supabase-export.md) | Founder-run Supabase (TeraKidz) export to B2 and sandbox restore |
| [teracorp-friday-rollout.md](teracorp-friday-rollout.md) | Roll out FRIDAY (SW1 software factory): GitHub tokens/labels/webhooks, branch protection, deploy the `friday` container and `friday_dispatch` worker, kill switch, rollback |
| [teracorp-midtrans-rollout.md](teracorp-midtrans-rollout.md) | PAY1: Midtrans QRIS/VA payments for Terakidz (keys, sandbox→production switch, monitoring, kill switch) |

For the dependency graph, see
[docs/service-map.md](../../docs/service-map.md).

Every `kctl-dokploy` command must use an explicit profile. Never stop or remove
the `dokploy` or `traefik` platform containers.
