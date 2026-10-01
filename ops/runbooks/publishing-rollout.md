# F10 / PUB1 — local publishing rollout

Status: code-ready, founder-gated, local only. Nothing in this runbook deploys a transport or
publishes to a social platform. Postiz is deliberately a later transport.

## M0 — founder gates before enabling the bundle

1. Confirm the company and brand kit that will own each account. The Odoo account must name one
   `factory_company_id`, one `factory_brand_kit_id`, one `factory_channel` and one engine
   `factory_publish_mode`; a mismatch refuses before a publication row is created.
2. Create one publisher account per brand and channel. Store only the 1Password item names in
   the rollout record, never credential values, access tokens or screenshots. Suggested item names
   (names only):

   - `Terakidz / TikTok / publisher credentials`
   - `Terakidz / YouTube / publisher credentials`
   - `Terakona / TikTok / publisher credentials` (placeholder until its brand kit is founder-approved)
   - `Terakona / YouTube / publisher credentials` (placeholder until its brand kit is founder-approved)

3. Confirm the account's publisher registry entry and capabilities. The current local modes are:

   | Channel | Current mode | Local meaning |
   |---|---|---|
   | TikTok | `creator_handoff` | prepare ends at `awaiting_creator`; the creator posts in-app |
   | YouTube | `private_remote_asset` | prepare creates a private remote asset; a manager verifies before make-live |

   `direct_publish` is not enabled by this slice. Changing a capability or mode is a separate
   founder decision and must not be smuggled into an account edit.

4. Record the Postiz trigger as a founder decision, not a default. Until a numeric value is
   approved, the transport remains disabled. Proposed gate: when approved publishing volume is
   above **[founder to set: publications per channel per UTC day]** for **[founder to set:
   consecutive days]**, review whether Postiz is justified. A future Postiz transport must
   implement the existing registry contract: account/capability discovery, prepare, explicit
   publish/make-live, poll/reconciliation for unknown outcomes, and metric snapshots. It must not
   add a second scheduler or metrics store.

## M1 — local installation and configuration

Install the bundle group only in a disposable/local Odoo test or founder-approved environment:

```sh
kctl-odoo local test factory_publish -d odoo_test_factory_publish_fresh --tags /factory_publish
```

The `private-factory:publishing` group depends on the F8 `content` install group and the publication
engine/review modules supplied by `private-content`. This bundle dependency describes module
installation only; it is not evidence that F8 has been deployed. The earlier serialized F10
neighbour run reported 3 failures and 44 errors of 115 tests; its full traceback and failing test
names were not retained. The later owner-captured F8 rerun is green: `factory_content`, 143
module-stat tests, 0 failures/errors (115 Odoo post-tests). This is owner-reported evidence, not
an F8 run performed by this F10 slice. An initial F10 attempt using a separately created
production-mode test DB had 14 test errors because test-only `content_account.fake_api_key` was
absent from that DB schema; direct `local test` on a fresh DB composed the test schema correctly.
Final F10 evidence is recorded in the publishing results ledger. No red result above is current
F8 acceptance evidence. The group is absent from the default profile.

Configure accounts using the Odoo UI/API with the founder-approved company, brand kit, channel
and mode. Do not paste credentials into source, YAML, logs, publication metadata or test
fixtures. The adapter metadata contains only a factory piece reference, channel, kind and aspect
ratio; copy text is sent to the engine caption field but is not duplicated in adapter metadata.

## M2 — manual-first drill (TikTok)

Use a non-sensitive released test piece and a fake/local account first.

1. Complete the F8 expert → founder release gate. A derived, submitted or cancelled piece must
   refuse scheduling.
2. As a publishing operator, explicitly schedule the released piece against the matching
   Terakidz/TikTok account. The operation key makes repeating this action idempotent.
3. Explicitly choose **Prepare**. The publication is claimed by the existing engine and ends at
   `awaiting_creator` for `creator_handoff`.
4. The creator posts in TikTok's app. `action_make_live` must refuse for this mode; there is no
   local “mark posted” shortcut in F10.
5. If prepare or make-live returns an unknown result, stop. Do not retry automatically; a person
   reconciles the claimed publication using the engine's unknown-outcome workflow.
6. Read metrics through the engine snapshot path. The metric cron's configured window suppresses
   duplicate polling while each accepted snapshot remains append-only and timestamped.

## M3 — rollback and evidence

To stop the local rollout, suspend/deactivate the publisher account and leave existing unknown
publications for human reconciliation. Do not delete publication rows, reset operation keys or
retry an unknown platform call by hand. Capture only IDs, states, timestamps, checksums and the
founder's decision; redact account names if they identify a person.

Before any founder approval, attach the F10 results ledger with the module and neighbour test
counts, `bin/validate-bundles`, `bin/lint-addon-i18n`, the fix-wave test output and exact local
commits. Record `built-local` only; no deployment or live-platform evidence is claimed here. No
push, deploy, Postiz connection or live-platform call is part of this slice.
