# P6 / MM1 — Mattermost → Odoo approval rollout (founder gate)

**Scope:** kodeme.io estate only. This is a preparation runbook, not authorization to deploy.
Do not use an idtpp service, host, credential, database or Mattermost tenant. The chosen initial
path is Odoo `mcp_mattermost` → `/mattermost/webhook/action`; the separate Teracorp plugin stays
inactive. The conditional architecture decision is in
`kodemeio-docs/superpowers/specs/2026-09-28-teracorp-p6-direct-odoo-launch-decision.md`.

## Hard prerequisites

1. Finish Wave 0 security and recovery gates: leaked-key rotation, admin access, backups,
   restore evidence, monitoring and a staging host. Stop if any prerequisite is unknown.
2. Founder approves the exact Odoo database, Mattermost team/instance, callback origin and
   human approver roster. Verify no idtpp endpoint appears in the resolved configuration.
3. Confirm the deployed Mattermost version preserves interactive-action context confidentiality
   and that button callbacks reach the intended Odoo TLS origin. Treat a mismatch as a stop
   condition; do not substitute an unauthenticated proxy or enable the plugin alongside Odoo.
4. Store the Odoo Mattermost signing secret in managed secret custody. Prove it is non-empty,
   scoped to this config, unavailable in card/client/log output, and has a documented rotation
   and emergency revocation procedure. Record only secret references, never values.
5. Verify each Mattermost user maps to exactly the intended active internal Odoo user and
   company; no bot, portal, key-owned, disabled or self-approving identity can pass the
   `mcp.operation` policy. Confirm private DM delivery and reviewer visibility.
6. Install/upgrade through the ordinary Odoo preflight path only after the independent P6
   acceptance suite and neighbor suites pass on a disposable local DB. Headless approval HTTP
   acceptance lives in the existing `mcp_mattermost` test suite; the rejected standalone
   test-only addon must not be installed or restored.

## Staging acceptance — record evidence for every row

Use synthetic prepared operations on a designated staging DB; never execute a real customer or
financial write as a smoke test. Capture timestamp, opaque operation ID, Mattermost/Odoo version,
reviewer role, observed HTTP outcome, Odoo state and redacted audit reference.

| Case | Required outcome |
|---|---|
| Correct reviewer clicks Approve | One pending operation becomes approved; no execution occurs from the click |
| Correct reviewer clicks Reject and submits a reason | Only that operation becomes rejected with the submitted reason |
| Wrong mapped user, bot, expired or tampered action | No decision; visible refusal and safe audit |
| Token from operation A presented while B is pending | A alone may be decided; B remains pending |
| Repeat click and two concurrent clicks | No second state transition, duplicate execution or conflicting decision |
| HTTP reply lost after Odoo commits | Re-read authoritative operation state; retry must not create another decision |
| Deactivate Mattermost config with action/dialog outstanding | Both callback routes refuse; no new approval through Mattermost |
| Signing key revoked/rotated | Old cards refuse; newly issued cards use only the approved key |
| Execute an approved synthetic operation | Exact prepared action hash/policy rechecked; changed arguments refuse |
| Inspect logs and metrics | No signed token, secret, customer payload or unredacted PII persists |

Also verify failure alerts, multiple Odoo workers, backup/restore of the approval state and the
founder-approved callback network policy. A local test suite does not substitute for these
staging checks. Keep the plugin inactive throughout the drill.

## Production gate and rollback

Founder and security reviewer sign the staging evidence and the final approver roster before
production rollout. Deploy only through the reviewed Odoo release procedure, then use one
bounded synthetic production check if explicitly approved. Enable monitoring for callback
refusals, latency, pending/expired operations and unexpected approval sources.

If actor binding, confidentiality, replay, audit or rollback checks fail: deactivate the
Mattermost config to stop **both** action and dialog callbacks; pause new MCP prepares by the
approved profile/key procedure; set the separate MCP execution kill switch to off before any
new execution. The execution kill switch does **not** itself revoke a pending approval or the
Odoo login approval page, so reconcile those states separately. Rotate the signing secret if
exposure is suspected, remove stale cards, and require a freshly prepared operation before
resuming. Do not infer a decision from a lost HTTP reply; query Odoo's authoritative state.

No production action, push, secret issuance, version check or live drill was performed while
writing this runbook. P6 stays non-operational until the evidence above is recorded.
