# idtpp Odoo URL map

Live state as of 2026-09-14. Desired state lives in [`idtpp.yaml`](idtpp.yaml).
Old URLs redirect to the new URL with the path and query kept.

| Env | Instance | Old URL(s) → redirect | New URL | Redirect | Server | Authentik provider | Moved |
|---|---|---|---|---|---|---|---|
| prod | tpp-odoo-erp | `tpp-odoo-erp.idtpp.com`, `tpp-erp.idtpp.com` | `https://erp.idtpp.com` | temporary | tpp-prod-03 | 11 | 2026-09-13 |
| prod | tpp-odoo-helpdesk | `helpdesk.idtpp.com`, `tpp-desk.idtpp.com` | `https://desk.idtpp.com` | temporary | tpp-prod-03 | 38 | 2026-09-13 |
| prod | tpp25-odoo-erp | `tpp25-odoo-erp.idtpp.com` | `https://tpp25-erp.idtpp.com` | temporary | tpp-prod-07 | 33 | 2026-09-13 |
| prod | mac-odoo-erp | `mac-odoo-erp.idtpp.com` | `https://mac-erp.idtpp.com` | temporary | tpp-prod-02 | 6 | 2026-09-13 |
| prod | mac-odoo-hrms | `mac-odoo-hrms.idtpp.com` | `https://mac-hrms.idtpp.com` | temporary | tpp-prod-02 | 7 | 2026-09-13 |
| prod | tpp-odoo-hrms | — (not renamed) | `https://tpp-odoo-hrms.idtpp.com` | — | tpp-prod-03 | 12 | — |
| staging | tpp-odoo-erp-stg | `tpp-odoo-erp-stg.idtpp.com`, `tpp-erp-stg.idtpp.com` | `https://erp-stg.idtpp.com` | temporary | tpp-prod-07 | 22 | 2026-09-13 |
| staging | mac-odoo-erp-stg | `mac-odoo-erp-stg.idtpp.com` | `https://mac-erp-stg.idtpp.com` | temporary | tpp-prod-07 | 24 | 2026-09-13 |
| staging | mac-odoo-hrms-stg | `mac-odoo-hrms-stg.idtpp.com` | `https://mac-hrms-stg.idtpp.com` | temporary | tpp-prod-07 | 25 | 2026-09-13 |
| staging | tpp-odoo-hrms-stg | — (not renamed) | `https://tpp-odoo-hrms-stg.idtpp.com` | — | tpp-prod-07 | 23 | — |
| staging | tpp25-odoo-erp-stg | — (not renamed) | `https://tpp25-odoo-erp-stg.idtpp.com` | — | tpp-prod-07 | 33 (shares prod client) | — |

- **Temporary** = HTTP 302 for GET, 307 for other methods. Planned: switch to permanent (301/308) around 2026-09-27, clean up around 2026-10-13. Old URLs keep redirecting after cleanup.
- **Not renamed**: those instances have no `kctl-odoo` profile yet.
- **Services repointed to the new URLs**: JARVIS MCP (`erp`, `desk`, `erp-stg`), `tpp-infra-hermes-04`, `tpp-accurate-sync` (`erp`), and the Mattermost helpdesk plugin (`desk`).
