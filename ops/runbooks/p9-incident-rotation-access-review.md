# P9 — Incident response, credential rotation, and quarterly access review

**Scope:** founder-operated governance for the kodeme.io (kod) estate.
This supplements, and does not replace, [the existing incident-response
runbook](incident-response.md), service-specific rollout procedures, or
provider policies. It is a local documentation foundation only: no incident
tabletop, credential rotation, permission review, contact, production access,
or deployment was performed. **Agents must not read secrets or production
`.env` files, contact people, exercise production, rotate credentials, or take
automated/live action.**

P9 remains not operational until the founder approves the register/cadence,
completes a staged tabletop, performs and records the required human reviews,
and attaches the evidence listed below. An empty register or this runbook is
not evidence of control.

## Responsibilities and least privilege

| Role | Responsibility | Access rule |
|---|---|---|
| Incident commander (founder or explicitly delegated human) | Set severity, authorize actions, coordinate escalation, accept closure | One named human; no shared identity |
| Service owner | Validate impact, approved recovery/rotation procedure, and post-change health for one service | Access only to that service and its approved admin functions |
| Credential custodian | Maintain opaque credential inventory references, owners, due dates, and rotation evidence | Vault access only to assigned items; never copy values into this repository or incident record |
| Reviewer/verifier | Independently check requested scope, membership, staged verification, and evidence | Read-only review unless separately authorized as an operator |
| Agent/automation | No incident commander, reviewer, recovery, or rotation authority | No secrets, production access, contact, rotation, or live action |

Keep human identity, application roles, service credentials, and host access as
separate entries. Grant the narrowest role for the shortest period; use named
accounts; separate routine and recovery identities; require the provider's
strongest supported MFA for privileged human identities; and avoid standing
global administrator access. Record exceptions, owner, reason, compensating
control, approver, and expiration date. Do not store credential values,
recovery codes, tokens, private keys, cookies, or secret-bearing command output
in the register or evidence.

## Incident handling and escalation

1. Follow `incident-response.md` for initial read-only diagnostics, deployment
   identifiers, logs, rollback target, and closeout. Preserve its P1/P2 rule:
   notify the operations channel before changing live state. If that channel is
   unavailable or implicated, use the founder-approved out-of-band contact
   register; no contacts are embedded here.
2. Name an incident commander, affected service owner, operator, and verifier
   where available. Assign one incident reference and use UTC timestamps.
3. Classify and escalate immediately to the founder if there is suspected
   credential exposure, account takeover, unexpected admin access, data
   exposure, loss of an essential control plane, or an unknown blast radius.
   Treat suspected privileged credential compromise as urgent; do not wait for
   the quarterly schedule.
4. Prefer read-only containment evidence first. Before any live mutation,
   record who authorized it, exact target and scope, intended result, expected
   impact, rollback, and stop conditions. Use an existing reviewed,
   service-specific runbook. This document is not authority to run a command.
5. Abort if the target or profile is ambiguous, the operation would expose a
   secret/customer data, the replacement cannot be verified, the rollback is
   unknown, or the action risks locking out the only authorized operator.
   Escalate rather than improvise.
6. Close only after access is rechecked, temporary grants are removed, the
   secret inventory is updated without values, service owners accept health,
   and the timeline/evidence/follow-ups are recorded. P1/P2 post-incident
   documents belong under `ops/runbooks/incidents/` per the existing runbook.

## Credential rotation calendar

There is no complete estate credential inventory or universal max-age policy
codified here. Do not invent one credential lifetime or rotate every secret
blindly: providers differ, and an uncoordinated rotation can cause an outage.
The founder must approve each class's routine maximum age and the specific
provider/service procedure in the register before claiming the schedule is
active.

| When | Required founder-owned action | Evidence |
|---|---|---|
| Immediately on suspected exposure, lost device/recovery factor, unauthorized use, or privileged operator departure | Open an incident; identify only the affected credential(s); revoke/replace through the approved provider procedure; verify dependent service and revoke old value after safe cutover. For suspected compromise, never roll back to the compromised value. | Incident reference, authorization, opaque item ID, timestamps, affected systems, sanitized verification, revocation confirmation |
| At onboarding, role change, or offboarding | Review the person's named accounts, groups, tokens and active sessions; remove unneeded access and rotate/reassign individually attributable credentials where necessary. | Access change record, reviewer, effective time, session/token disposition |
| First business week of January, April, July, and October | Conduct the quarterly access review below; inspect all credential due dates and provider notices; assign a dated maintenance window to every item due before the next review. | Completed quarterly review sheet and approved rotation work items |
| By each register item's founder-approved `rotation_due_utc` (set no later than its approved `max_age_days` or provider expiry) | Rotate the named credential using its service-specific, founder-approved process; verify new credential in stage first where supported, perform a controlled cutover, verify the dependent service, then revoke the old value. | Change reference, operator/verifier, due date, stage result, cutover/old-value revocation confirmation, sanitized health result |
| If there is no approved max age, owner, or supported overlap/rollback procedure | Mark `UNSCHEDULED—FOUNDER DECISION REQUIRED`; escalate for a decision. Do not count the item as reviewed/rotated or make an agent choose a lifetime. | Decision owner and due date; explicit exception and compensating control |

The quarterly date above is a proposed local calendar convention and requires
founder approval before adoption. Per-credential due dates are not supplied by
this repo and must be approved based on the provider's current policy and
operational impact. Staging uses synthetic credentials only; never reuse a
production credential in a test. Keep old/new secret values out of tickets,
shell history, logs, chat, screenshots, and git.

## Quarterly permission review

The founder schedules the review during the first business week of each
calendar quarter. Review Authentik, Odoo, Mattermost, Dokploy, host/SSH access,
service accounts, API tokens, deployment identities, and any other control
plane inventoried for kodeme.io. Do not infer that an unlisted system has no
privileged access.

1. [ ] Record quarter, reviewer, service owners, inventory snapshot time, and
   scope. Gather roles/memberships from approved read-only interfaces or
   sanitized exports; never export secret values or customer records.
2. [ ] For every human identity, verify named owner, business need, service,
   exact role/group, MFA status where available, last-used/last-reviewed
   evidence where available, and employment/engagement status through the
   approved owner process.
3. [ ] For every service identity/token, verify one accountable owner, one
   service/purpose, minimum scopes, storage reference (opaque vault item ID),
   last rotation and approved next due date, expiry, and whether it is still
   used. No owner or purpose means flag for founder decision, not auto-delete.
4. [ ] Compare standing administrator membership with the least-privilege
   target in [P7](p7-break-glass-access-recovery.md). Confirm recovery access
   is separate, not shared, and has a documented route; record missing paths
   as gaps, not passes.
5. [ ] Check exceptions for approver, reason, compensating control, and
   expiration. Mark stale/unused or unexplained grants for human disposition;
   do not revoke a live identity or token from this checklist automatically.
6. [ ] Review proposed changes with the service owner. Stage role/permission
   changes against synthetic accounts where supported; verify expected allow
   and deny behavior before the founder separately authorizes a production
   change under the relevant runbook.
7. [ ] Record decisions and named human owners/due dates. The founder signs
   the result and explicitly accepts or remediates each exception. Recheck
   after separately authorized changes and record the observed final state.

## Staged/synthetic tabletop checklist

This checklist describes a future founder-run tabletop using fictitious names,
synthetic credential references, and non-routable examples. It does not send
messages or alter access. Do not use a real secret, real customer case, or live
provider session.

- [ ] Scenario: synthetic privileged token appears in a fictitious log. The
  group identifies affected systems from a mock register, classifies urgency,
  and selects the approved escalation path without revealing a value.
- [ ] Scenario: one synthetic operator leaves. The group finds every mock
  named account and owned service credential, assigns a human disposition,
  and does not assume rotating a shared token identifies its owner.
- [ ] Scenario: replacement credential fails in a stage fixture. The group
  stops cutover, follows the documented rollback without restoring a
  compromised value, and names the escalation owner.
- [ ] Scenario: unexpected administrator membership appears in a synthetic
  access export. The reviewer documents evidence, scope, owner, and decision;
  no automatic deletion occurs.
- [ ] Verify P1/P2 escalation preserves the existing notify-before-live-change
  policy and uses the approved out-of-band route only if the operations channel
  is unavailable/implicated.
- [ ] Record where the existing procedure is absent, ambiguous, or unsafe;
  assign an owner and due date. A tabletop pass does not prove a real rotation
  or access review occurred.

## Abort, rollback, and escalation

Stop an in-progress rotation or permission change before cutover if the
replacement is not available in the approved vault, stage verification fails,
the dependent service is unhealthy, the change scope differs from approval,
or rollback would restore a suspected compromised secret. If a live change is
already authorized and health degrades, follow that service's reviewed
rollback procedure and the P1/P2 escalation policy. Do not remove an account,
revoke a token, rotate a secret, disable SSO, or deploy based solely on this
runbook. Escalate unclear ownership, missing procedure, or unresolved access
to the founder and service owner. Use provider support through the approved
contact register when necessary; agents must not contact anyone.

## Evidence fields and acceptance

Keep evidence in the founder-approved restricted incident/audit location; link
by opaque reference only. Redact before attaching.

| Field | Record |
|---|---|
| Review/tabletop/incident reference and quarter | |
| Date and UTC start/end | |
| Incident commander / reviewer / operator / verifier | |
| Services and identity/credential classes in scope | |
| Inventory snapshot reference (no secret-bearing export) | |
| Finding, severity, affected scope, and sanitized evidence reference | |
| Authorization reference and exact approved action | |
| Credential opaque item ID, owner, purpose, approved age/due date | |
| Stage result; cutover/revocation result if separately performed | |
| Access role decision; exception approver, control, and expiration | |
| Abort/rollback outcome and final health/access check | |
| Follow-up owner, due date, status | |
| Founder acceptance and date | |

**P9 acceptance requires:** founder approval of the quarterly calendar and
per-credential due-date policy; a completed staged tabletop with recorded
findings; a complete owner-reviewed access/credential inventory; dated
quarterly permission review evidence; and closure or explicit acceptance of
each exception. No such founder evidence is present in this change. P9 is not
operational, no rotation is claimed, and no drill/review is claimed performed.
