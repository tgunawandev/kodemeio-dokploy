# P7 — Break-glass access and recovery

**Scope:** kodeme.io estate only: Authentik, Odoo, Mattermost, and Dokploy.
This is a founder-operated procedure and evidence template, not proof that any
recovery path works today. No path may be used to bypass application
authentication or authorization. **Agents must not exercise live access,
connect to production, use credentials, or perform recovery actions.** The
founder owns every staged or live drill and all state-changing steps.

This runbook supplies a local documentation foundation; P7 remains not
operational until each service has a founder-approved recovery route and dated
staged and production evidence. The normal
Dokploy admin-gate check in `ops/scripts/check-admin-gates.sh` proves only
unauthenticated HTTP responses; it does not prove a break-glass login, correct
role membership, or recovery access.

## Roles and access boundaries

| Role | Allowed responsibility | Must not receive |
|---|---|---|
| Incident commander (founder or explicitly delegated human) | Declare incident severity, authorize a narrowly scoped live action, approve rollback, close the incident | Shared or undocumented credentials |
| Recovery operator (named human) | Perform only the approved service-specific recovery procedure | Broad estate access merely for convenience |
| Independent verifier (second named human, when available) | Confirm access outcome, intended role, and evidence before closure | Permission to make unreviewed changes as a verifier |
| Evidence recorder | Record timestamps, identifiers, sanitized outputs, and disposition | Secret values, recovery codes, tokens, session cookies, or customer data |
| Agent/automation | No recovery authority | Production login, secret access, live tests, credential rotation, deploys, or approval |

Use named human accounts for routine administration. A recovery identity, if a
service supports one, must be separately named, tightly scoped to that service,
protected by the provider-supported strong authentication, held in the
founder-controlled approved vault, and monitored. Do not share a personal
account, create a universal admin, or assume an identity exists because a
runbook calls for one. Record its opaque vault item reference, never its value.

| Service | Least-privilege target | Repository-supported recovery fact / gap |
|---|---|---|
| Authentik | One named identity administrator for identity recovery; normal operators retain only the groups and app access required for their duties | No independent recovery account or approved bypass is codified here. The isolated Authentik restore drill tests restored synthetic-user login, not production break-glass access. Founder must document and stage the supported route before claiming P7 coverage. |
| Odoo | Named Odoo administrator limited to the required Odoo database/instance; no routine database-superuser or host-root access | No Odoo break-glass path is codified here. Founder must identify a supported account-recovery route and demonstrate it in a disposable database. Do not infer that shell/database access is an approved bypass. |
| Mattermost | Named system administrator only for Mattermost administration; ordinary users retain their normal team/channel roles | No independent Mattermost recovery path is codified here. Founder must confirm the configured authentication mode and stage the supported route without weakening SSO or broadening roles. |
| Dokploy | Named authorized host operator only when required for the control plane; normal deploy operators use their assigned Dokploy permissions | `ops/traefik/dokploy-admin-gate.yml` documents an SSH tunnel to Dokploy's host-local port 3000. This bypasses Traefik/forward-auth, **not Dokploy's own login or authorization**. `teracorp-wave0-rollout.md` G8 requires proving the path before applying the gate. Host access scope and current usability still require founder verification. |

If the current provider supports a lower-privilege or audited recovery method,
prefer it over the target role above and record the actual role and reason for
any exception. A direct database edit, shared root login, disabling SSO, or
opening an admin route is not an approved fallback by implication.

## Preconditions

Before a founder schedules a drill or uses this procedure:

- Confirm the affected service, owner, severity, approved profile, and the
  smallest required recovery action. For P1/P2, preserve the existing
  `incident-response.md` rule to notify the operations channel before changing
  live state, unless that channel itself is unavailable; then use the approved
  out-of-band escalation contact.
- Identify the documented service-specific recovery route, its authentication
  factors, expected role, time-bounded access window, and revocation/rollback
  method. If any item is unknown, stop at read-only triage and escalate to the
  founder; do not improvise a bypass.
- For staged testing, use an isolated non-production environment, synthetic
  accounts/data, and a test-only identity provider or fixture. Confirm the
  hostname, profile, network, and account are not production before proceeding.
- Have an independent verifier where practical. If the founder is the only
  operator, record that separation-of-duties limitation and require a second
  review before marking evidence accepted.
- Prepare an evidence destination with restricted access and a redaction
  check. Do not capture secrets, recovery codes, session cookies, full env
  output, customer records, or unredacted screenshots.
- Confirm a rollback owner and stop condition. Recovery access is temporary:
  close the session, revoke temporary grants, and verify the normal gate again.

## Staged/synthetic verification checklist

These are planning and acceptance checks, not performed results. The founder
must run them in the isolated stage and attach evidence before considering a
production drill. No production drill is authorized by this document.

1. [ ] Record environment name, synthetic account IDs, build/config revision,
   start time (UTC), operator, verifier, and ticket/incident reference.
2. [ ] From a clean client with no session, verify the synthetic admin route is
   denied or redirected to the expected identity provider. For a local copy of
   the Traefik gate, run `bash ops/traefik/tests/test_gates.sh`; this test uses
   local stand-ins and must not be pointed at production. Record its output and
   whether Docker was available (a skipped test is not a pass).
3. [ ] Sign in using only the designated synthetic recovery identity and its
   documented factor. Verify the expected service and no broader estate access.
4. [ ] Sign in as a synthetic ordinary user and confirm recovery-only actions
   remain unavailable. Confirm an unknown/unauthorized account is denied.
5. [ ] Exercise only a reversible, non-customer-facing read or harmless
   synthetic action. Do not change a live gate, password, MFA factor, role,
   configuration, deployment, or customer data during this checklist.
6. [ ] For Dokploy, use the local gate test for behavior. Treat the documented
   `ssh -L 3000:localhost:3000 <dokploy-host>` route as a founder-only
   production contingency described by G8, not as something this test ran.
   Do not attempt a host tunnel as part of this documentation task.
7. [ ] End the session; remove any stage-only grant/account; verify the normal
   gate and ordinary-user denial still hold; record cleanup confirmation.
8. [ ] Have the verifier compare evidence to the acceptance criteria below.
   Record failures as failures and leave the path unapproved until corrected.

## Incident use, abort, and rollback

1. Start with [incident-response.md](incident-response.md) and use
   [P9's incident/rotation procedure](p9-incident-rotation-access-review.md)
   for severity, escalation, and evidence fields.
2. Prefer read-only diagnostics. The incident commander authorizes one named
   operator, one service, one action, and a time limit; log the authorization
   before mutation when feasible.
3. Use only the service-specific, founder-approved route. Do not disable
   Authentik, remove a Traefik middleware, open a public port, weaken MFA, use
   another service's credential, or broaden roles as a shortcut.
4. **Abort immediately** if the target/profile/environment is ambiguous; the
   expected identity, route, role, or verifier differs; normal or synthetic
   users gain unexpected access; evidence may contain a secret; service health
   degrades; or the approved time window expires.
5. Stop the session, revoke temporary access, and restore the last known
   approved access policy using the service's reviewed rollback procedure.
   For Dokploy gate changes, follow G8's rollback in
   `teracorp-wave0-rollout.md`; this document does not authorize that change.
   If rollback cannot be verified, stop further changes and escalate to the
   founder/provider support using the approved contact register.
6. Verify normal access gates and service health with the established
   read-only checks. A clean `check-admin-gates.sh` result alone is not proof
   of successful break-glass authentication.

## Evidence record and acceptance

Record one entry per service and per drill. Do not mark any entry complete
until the founder has reviewed the evidence.

| Field | Record |
|---|---|
| Service / environment | |
| Drill or incident reference | |
| Date and start/end time (UTC) | |
| Operator / incident commander / verifier | |
| Recovery identity reference (opaque vault item ID only) | |
| Approved role and exact scope | |
| Recovery route and revision/date reviewed | |
| Synthetic account IDs / data fixture reference | |
| Expected result and observed result | |
| Gate-check output or sanitized evidence reference | |
| Ordinary-user and unauthorized-user denial results | |
| Temporary grants removed / session ended / time | |
| Normal gate and service health rechecked | |
| Exceptions, failure, abort reason, escalation | |
| Founder acceptance / date / follow-up owner and due date | |

**Acceptance requires:** an independently reviewed staged pass; a documented
service-specific route; expected least-privilege role; ordinary-user denial;
temporary access cleanup; normal gate restored; and a founder-recorded
production confirmation only if/when the founder separately performs an
authorized production drill. As of this local documentation change, none of
those founder evidence items is asserted as complete. P7 is not operational.

## Escalation

Escalate an unknown or failed route to the founder/incident commander
immediately; escalate suspected credential compromise or unintended access as
P1/P2 under the existing incident runbook. Use only the approved out-of-band
contact register (do not place personal contact details in this repository).
If the founder or identity administrator is unreachable, preserve service
state, keep the gate closed, capture sanitized evidence, and contact the
provider through the founder-approved support channel. Never ask an agent to
retrieve credentials, test production, or take over a recovery action.
