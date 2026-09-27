# G5 — Contractor onboarding through Authentik + Mattermost

**Status: local preparedness only.** This procedure is for a founder to execute later after the
policy and service-owner gates below are satisfied. No contractor account, group, MFA factor,
Mattermost membership, credential, service, or production state was accessed or changed to prepare
this document. Do not treat the synthetic harness as an account test or evidence of deployment.

G5 is blocked until the founder explicitly sets the founder-hours threshold and its measurement
policy. The threshold is intentionally blank in
[`examples/teracorp-g5-unconfigured.synthetic.json`](examples/teracorp-g5-unconfigured.synthetic.json).
No value is implied or recommended here. The validator must refuse onboarding while it is unset.

## Authority and prerequisites

Only the founder (or a named human delegate explicitly authorized in the restricted decision record)
may approve hiring, scope, role, access duration, and service changes. An automation/agent may not
create, invite, promote, reset, or revoke an account. Use a second named verifier where available;
record any single-operator exception and require a later independent check.

Before onboarding, the founder must complete all of these decisions and retain the evidence in the
founder-approved restricted audit location (opaque references only in tickets/repository):

1. Approve the weekly-hours trigger: numeric threshold, strict `>` comparator, unit, measurement
   window, how G3's weekly metric is aggregated, treatment of missing weeks, evidence owner, and
   decision date. Add the signed decision reference to the protected record. The local harness
   accepts an explicit value only to test the contract; this repository's checked-in example stays
   unconfigured. Never derive a threshold from the synthetic tests.
2. Verify the trigger is exceeded using reviewed real founder-hours records for the approved period.
   Record period, source reference, measured result, independent reviewer, and review date. Synthetic
   samples and a single unreviewed estimate do not qualify.
3. Approve a specific contractor, sponsor, work statement, data classification, systems, exact
   permissions, start/end UTC, and budget. Complete legal/privacy and contract checks applicable to
   the work before account creation.
4. Ask the Authentik and Mattermost service owners to verify current configuration in an isolated
   approved test environment. Confirm the precise low-privilege Authentik group-to-application
   mapping, group inheritance, MFA policy/enforcement, identity-disable effect, session invalidation,
   and supported audit events. Repository role names do not prove runtime entitlements. If no exact
   non-admin mapping fits, stop for a separately reviewed role/configuration change.
5. Approve the exact Mattermost team and private project channel(s), membership list, posting/read
   needs, and removal path. Suggested names are private `teracorp-<project>-contractors`, plus
   `teracorp-onboarding` only when a separate private coordination channel is needed. These are
   recommendations, not existing or created channels; check for collisions and approve actual IDs.
6. Establish where restricted audit evidence lives, who can read it, retention/deletion rules,
   approver/operator/verifier responsibilities, and an expiry/review reminder owner. Never put names,
   emails, contract content, secrets, recovery codes, MFA seeds, tokens, session cookies, or raw
   membership exports in this repository.

If any prerequisite is absent, the result is **STOP — NOT APPROVED**. Do not create a placeholder
user or grant access “temporarily”.

## Least-privilege assignment matrix

| Surface | Exact target to approve | Excluded by default | Evidence to verify |
|---|---|---|---|
| Authentik identity | One named human principal, one founder-approved existing low-privilege application group, only the mapped application(s) needed for the work | Platform/company admin, DevOps/infrastructure, service-account groups, unrelated tenant/application groups, shared identities | Exact group ID, effective inherited groups, mapped app/resource scope, approver, sanitized allow/deny check |
| Mattermost | Ordinary team member; membership only in named private project channel(s), plus private onboarding coordination only if separately justified | Team/system admin, public/general channels by default, unrelated projects/customer channels, broad default-team membership | Team/channel IDs, private flag, effective role, membership list comparison and removal procedure |
| MFA | Provider-enforced strongest approved human factor on each sign-in path | MFA bypass, shared/reused factors, factor seeds or recovery codes in tickets | Sanitized enrollment and enforcement evidence, verifier, timestamp |
| Time limit | Fixed UTC start and end approved for this engagement; removal/expiry reminder assigned to a named human | Indefinite access, automatic renewal, extending expiry without new approval | Effective expiry method and proof, review reminder owner, verifier |

No concrete contractor group is asserted to exist today. The `ak-kod-app-contractor-readonly`
string in unit tests is a fictional synthetic value, not a proposed production group and not an
instruction to create one. If an exact existing group cannot be shown to have only the approved
scope, stop and submit a separately reviewed service-configuration change.

## Joiner — founder executes, human verifier checks

1. Open a restricted access request with a unique opaque request ID. Attach founder decision,
   approved trigger-period evidence, engagement approval, data/system scope, role mapping evidence,
   and exact UTC end time. Verify the measured founder-hours strictly exceed the approved threshold.
2. Independently verify the contractor identity using the founder-approved process. Create a named,
   individual identity in Authentik (never a shared or service account). Record only the opaque
   subject reference in evidence.
3. Grant only the one verified low-privilege Authentik group needed for the approved application
   scope. Inspect effective/inherited groups and app entitlements after grant. Never grant
   `ak-platform-admin`, `ak-platform-devops`, any company admin group, or any `ak-svc-*` group. If
   mapping is uncertain or includes unrelated tenant/infrastructure access, stop and remove nothing
   automatically; request a reviewed scope decision before proceeding.
4. Enroll and verify the founder-approved strongest supported MFA for the person. Confirm that the
   actual sign-in path enforces it for Authentik and the connected Mattermost application. Do not
   retain a factor seed or recovery code. If enforcement or safe recovery cannot be demonstrated,
   do not continue.
5. Set the approved account/group access start and fixed expiry through the provider-supported
   method, then independently verify the effective expiry and calendar reminder. Confirm whether
   Authentik expiry also disables app sessions; assume it does **not** until the service owner proves
   otherwise. Capture the provider's non-secret evidence reference.
6. Add the person only to the approved private Mattermost team/channel(s), without team-admin or
   system-admin rights. Check actual channel privacy and membership. Disable self-service access to
   unrelated channels if the platform's current permissions allow it; do not broaden team defaults.
7. Ask the verifier to compare requested vs effective Authentik groups, MFA state, expiry, Mattermost
   channel privacy/membership, and an ordinary-user/admin-denial check in stage. Record sanitized
   evidence and discrepancies. Do not expose customer data during the check.
8. Close the request only after the verifier confirms exact scope and expiry. Record an access
   ledger event using opaque references; schedule a review before expiry. Never extend by silently
   editing the end date.

## Mover — scope changes are new approvals

1. Open a new request referencing the prior opaque access record. Obtain founder approval for the new
   work, classification, services, role and channel set, and new fixed expiry before any change.
2. The operator records exact old and new scope references. Verifier confirms the new Authentik
   effective group set and private Mattermost membership against the approved target.
3. Remove superseded groups/channels in the same approved change window; verify removal and any
   resulting sessions/tokens. Do not retain old and new access “just in case”. If atomicity or
   session effects are unclear, stop and escalate to the service owner.
4. Record approver, operator, verifier, timestamps, reason reference, old/new scope references, expiry,
   and sanitized add/remove evidence. Re-run the authorization review after change. A change to
   privileged access requires a distinct security review and is not covered by this runbook.

## Leaver / engagement end — revoke and verify all surfaces

Start at contract end, early termination, suspected compromise, or founder direction; use the
earliest approved effective time. For suspected compromise use the incident runbook as well.

1. Open a restricted leaver request and name the operator/verifier. Identify the contractor's
   opaque subject reference and inventory every Authentik group, Mattermost team/channel membership,
   active session, personal access token, bot/integration token, and separately owned credential
   within the approved scope. Do not infer there are no tokens because none appear in a chat record.
2. At the approved effective time, disable/revoke the Authentik identity and remove its G5 grants
   using the service owner's verified procedure. Verify the identity cannot authenticate and inspect
   residual group/app assignments.
3. Revoke Mattermost sessions using the verified supported procedure, remove team/channel
   memberships, and verify the user cannot access the private channels. Identity disable alone is
   not proof that existing sessions ended.
4. For each separately owned token/credential, follow its owning service's reviewed revoke/rotate
   process. If none existed, the service owner/reviewer records an evidence-backed `not applicable`
   disposition. Never paste the credential or token into the record.
5. Verifier independently confirms the Authentik account/grants, Mattermost memberships and
   sessions, and each token disposition. Preserve only approved business records under the approved
   retention policy; transfer ownership before removing a shared resource owner. Do not delete a
   person’s business data without the data owner and retention decision.
6. Close with the UTC effective time, operator/verifier, opaque evidence references, exceptions,
   and follow-up owner. Any unverifiable surface remains open and access removal is escalated to its
   service owner; do not claim the leaver is complete.

## Abort, rollback, renewal

Abort before grant if the trigger policy/evidence is missing, role inheritance is unclear, MFA is not
enforced, private channel status is uncertain, identity ownership cannot be confirmed, expiry cannot
be enforced/monitored, or independent verification fails. If a grant has already occurred, keep
scope minimal, notify the founder and service owner, remove only the exact unauthorized grant using
the approved procedure, verify session effects, and record the event. Do not disable SSO or weaken
MFA to recover access. Renewal is a fresh request with a new end date and founder approval; an
expired access record never self-renews.

## Audit evidence contract

Keep source evidence in the restricted founder-approved audit location, with least-privilege readers
and approved retention. The code harness accepts opaque references only. Capture, per lifecycle event:

- request/engagement decision reference; policy decision and trigger period/source/reviewer reference;
- opaque person/request reference, lifecycle action, scope/data-classification reference;
- exact Authentik group and verified group-to-resource mapping reference, approver, operator,
  verifier, timestamps, and effective-entitlement check reference;
- MFA enrolled/enforced verification reference (not factor material);
- Mattermost team and private channel opaque IDs, privacy/membership verification reference;
- UTC access start/end, expiry/enforcement evidence reference, review/reminder owner;
- mover's old/new scopes and approval; leaver's per-surface revocation time/evidence or documented
  `not applicable` reason for each token/session surface;
- exceptions, aborts, residual risks, remediation owner/due date, and final founder acceptance.

Audit references are not proof by themselves: the named human verifier must compare them to the
provider's sanitized evidence and attest the result. Store no credentials or unnecessary personal
data in the evidence package.

## Offline synthetic harness

From `kodemeio-dokploy`, run:

```sh
python3 -m pytest -q deploys/tests/test_teracorp_contractor_readiness.py
python3 ops/scripts/teracorp_contractor_readiness.py ops/runbooks/examples/teracorp-g5-unconfigured.synthetic.json
```

The pytest command must pass. The CLI on the checked-in unconfigured synthetic example is expected
to exit 2 with `founder-hours threshold is unconfigured`. Do not replace that example with a real
threshold or real identity data. Other synthetic in-memory cases test the contract and exercise
missing role, expiry, MFA, mover approval, and Authentik/Mattermost/token revocation evidence. They
do not call either service and do not create a test account.

**G5 is not operational** until the founder makes the trigger decision, conducts isolated approved
synthetic account lifecycle tests against verified service controls, executes the procedure for an
approved engagement, and independently accepts restricted audit evidence. No live test account or
external gate is claimed complete by this local-only slice.
