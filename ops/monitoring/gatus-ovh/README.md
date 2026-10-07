# Kodeme OVH Gatus

Deployment `kod-infra-gatus-ovh` (`TArUcMCkn5KFSlqrzx-ij`) runs on `kod-ovh-01`.
The UI and status API at https://gatus.kodeme.io use native Basic authentication
with a bcrypt password hash in the private Dokploy environment; `/health` is a
public health probe. No credentials, application bodies or customer endpoints
are stored in these checked-in monitor definitions.

SQLite persists ten result/event samples per endpoint. Checks run every minute
for Authentik, Odoo ERP/Desk, Mattermost, Dokploy and GlitchTip, checking HTTP 200
and at least seven days until certificate expiry. No duplicate notifications or
heartbeat service were added. The existing independent HZ Gatus compose
`RLyvhEXQhvPFrTgb2ZQ1V` and its alerts remain unchanged.

SENTINEL's private `operations_read` MCP adapter exposes `gatus.status` with the
fixed URL/profile and bounded positive field selection (identity, timestamp,
success and duration history only). Gatus itself has no native MCP server.
Credentials live only in the backend configuration. JARVIS has no backend bearer.
The Kodeme monitoring runbook and role policies are owned by `kodemeio-hermes`.

Auto-deploy is disabled on the new OVH compose. Manifest:
`deploys/instances/production/kod-infra-gatus-ovh.yaml`. Private ignored env:
`deploys/env/production/.env.kod-infra-gatus-ovh`. Database and volume backup
coverage require separate verified enrollment; no restoration claim is made.
