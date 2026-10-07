# Operational runbooks

Only reviewed runbooks compatible with the current `kctl` command surface live
here. Older procedures are retained under `docs/archive/runbooks/` and are not
safe operating instructions.

| Runbook | Use |
|---|---|
| [incident-response.md](incident-response.md) | Initial triage, evidence collection, and escalation |
| [p7-break-glass-access-recovery.md](p7-break-glass-access-recovery.md) | P7: founder-led recovery paths and staged access-gate verification |
| [p9-incident-rotation-access-review.md](p9-incident-rotation-access-review.md) | P9: incident coordination, credential rotation calendar, and quarterly access review |
| [postgres-restore.md](postgres-restore.md) | Restore a compose-embedded PostgreSQL database |
| [mattermost-sg-migration.md](mattermost-sg-migration.md) | Reviewed Mattermost Singapore migration |
| [jitsi-sg-migration-execution-2026-10-05.md](jitsi-sg-migration-execution-2026-10-05.md) | Completed Jitsi Singapore migration: production placement, latency, swap, Mattermost validation, deployment, and rollback |
| [jitsi-sg-migration-assessment-2026-10-05.md](jitsi-sg-migration-assessment-2026-10-05.md) | Jitsi capacity and migration assessment before execution; current state is in the execution report |
| [hetzner-disk-resize.md](hetzner-disk-resize.md) | Resize Hetzner disks and filesystems |
| [kod-ovh-01.md](kod-ovh-01.md) | Provision and operate the OVHcloud host `kod-ovh-01`: access, hardening, OVH API rebuild recipe, Dokploy registration |
| [odoo-mcp-governance-rollout.md](odoo-mcp-governance-rollout.md) | Install/upgrade the MCP governance addons (fail-closed binding, kill switch, rollback) |
| [chatwoot-host.md](chatwoot-host.md) | CW1: provision the Chatwoot/kido_chat host (M1) |
| [chatwoot-meta-whatsapp.md](chatwoot-meta-whatsapp.md) | CW1: Meta Business Manager, WABA, number, template (M2/M3) |
| [chatwoot-privacy.md](chatwoot-privacy.md) | CW1: privacy/consent/retention draft for counsel (M5) |
| [chatwoot-rollout.md](chatwoot-rollout.md) | CW1: deploy order, kill switches, handback, rollback (M6/M7) |
| [wave0-rollout.md](wave0-rollout.md) | Founder-gated Wave 0 rollout (G1–G10): B2 offsite, Healthchecks, Gatus, admin gates, drills |
| [wave0-s0.md](wave0-s0.md) | Founder-gated Wave 0 S0 (rows 0.1–0.3): dangling DNS removal, leaked GitHub App key rotation, kod Postgres ports + Hetzner delete protection |
| [kod-offsite-backup.md](kod-offsite-backup.md) | kodeme.io backups: what is copied where, and restore when Hetzner is down |
| [restore-drill.md](restore-drill.md) | Timed, isolated Odoo + Authentik restore drill from B2; record RPO/RTO |
| [supabase-export.md](supabase-export.md) | Founder-run Supabase (TeraKidz) export to B2 and sandbox restore |
| [friday-rollout.md](friday-rollout.md) | Roll out FRIDAY (SW1 software factory): GitHub tokens/labels/webhooks, branch protection, deploy the `friday` container and `friday_dispatch` worker, kill switch, rollback |
| [midtrans-rollout.md](midtrans-rollout.md) | PAY1: Midtrans QRIS/VA payments for Terakidz (keys, sandbox→production switch, monitoring, kill switch) |
| [factory-website-terakidz.md](factory-website-terakidz.md) | FC1–FC5 + F2: install the factory commons, push the brand kits, govern the Terakidz site, deploy the landing renderer, smoke test and roll back |
| [digital-delivery-rollout.md](digital-delivery-rollout.md) | R6 / DIG1: attach a released factory artefact, deliver paid bytes once, and revoke on full refund |

For the dependency graph, see
[docs/service-map.md](../../docs/service-map.md).

Every `kctl-dokploy` command must use an explicit profile. Never stop or remove
the `dokploy` or `traefik` platform containers.
