# Teracorp PAY1 — Midtrans payments rollout (Terakidz, kod-odoo-erp)

Rolls out Midtrans QRIS/VA payments for Terakidz orders on `erp.kodeme.io`
(tenant `kod`, kodeme.io Dokploy, compose `y0gfh3PIrT9lDn_OdSUEI`). Scope is
**kodeme.io only** — idtpp's own `payment_midtrans` install must never change
(spec D1).

- Design: `kodemeio-docs/superpowers/specs/2026-09-26-teracorp-pay1-midtrans-design.md`
- Results (P1–P10 evidence): `kodemeio-docs/superpowers/specs/2026-09-26-teracorp-pay1-results.md`
  (the plan ledger and the Task 6 slice rig notes live under
  `kodemeio-docs/.superpowers/sdd/`, which is **gitignored** — nothing here
  depends on them; they are pointers for whoever has that checkout, not
  tracked evidence)
- Code and full behaviour reference: kodemeio-odoo
  `src/private/integrations/payment_midtrans_guard/CLAUDE.md` — read it before
  touching the guard's models; this runbook only summarizes the parts an
  operator needs.
- Local setup facts (from the Task 6 slice rig, `bin/teracorp_slice_setup.py`):
  the rig's own notes live under `kodemeio-docs/.superpowers/sdd/` (**gitignored**,
  see above) — the operatively relevant facts are: the rig runs against a
  disposable local DB with a **fake** Midtrans server, and nothing there is a
  production credential or host.

Commands below run from the `kodemeio-odoo` checkout through `./odoo.sh`
(never raw SQL — `ir.config_parameter` is cached per worker and only
`set_param` invalidates it) and from `kodemeio-dokploy` through
`kctl-dokploy -p kodemeio`. `./odoo.sh addon`/`./odoo.sh release` work with
tenant `kod` even though their own `--help` text still lists only
`{tpp, tpp25, mac, mac-hrms}` — verified with `--dry-run` while writing this
runbook (`odoo.yaml` carries `kod`'s own `dokploy_profile: kodemeio` +
`compose: y0gfh3PIrT9lDn_OdSUEI`, so both resolve correctly).

## Account and keys

| Item | Value |
|---|---|
| Merchant account | Midtrans account for Teracorp/Terakidz. Dashboard login and Merchant ID: **PENDING — founder/ops owns Midtrans onboarding; fill in once the account exists.** |
| Sandbox keys | 1Password vault `Kodemeio`, item `midtrans: terakidz sandbox` — Server key `SB-Mid-server-...`, Client key `SB-Mid-client-...`. **PENDING — create this item when the sandbox account is issued; never paste keys into this file, a commit, or `manifests/`.** |
| Production keys | 1Password vault `Kodemeio`, item `midtrans: terakidz production` — Server key `Mid-server-...`, Client key `Mid-client-...`. **PENDING — create only at the production cutover (Stage 4 below), never earlier.** |
| Notification URL | `https://<erp host>/midtrans/webhook` — for `kod-odoo-erp` this is `https://erp.kodeme.io/midtrans/webhook`. Set it in the Midtrans **Dashboard → Settings → Configuration → Payment Notification URL**. Midtrans Sandbox and Production are separate portal environments with **separate** notification-URL settings — set it in both, once each key pair exists. |
| Route | The controller is a `type="json"` route and always answers Midtrans HTTP 200 (its own retry schedule is driven by non-2xx/timeout, never by the JSON body) — see the guard CLAUDE.md, "N5". `erp.kodeme.io` is fronted by Dokploy's Traefik reverse proxy, never a directly exposed port (infra rule) — verify with `kctl-dokploy -p kodemeio compose domains get y0gfh3PIrT9lDn_OdSUEI` before relying on the URL. |
| Provider per company | **Exactly one** `payment.provider` (`code=midtrans`) per company. The guard refuses a notification whose transaction's company differs from the resolved provider's company (spec D5, P9) — sharing one provider record across two companies is never correct, even if credentials are shared. If a second Teracorp company is added later, repeat Stage 2 below for it; do not reuse Terakidz's provider row. |
| Upstream seed provider | `payment_midtrans`'s own data (`data/payment_provider_data.xml`) seeds **one** provider named "Midtrans" (`state=test`, PLACEHOLDER credentials, `midtrans_environment=sandbox`) plus a bank journal named "Midtrans" (code `MDTRS`) — both on `base.main_company`. If Terakidz is `base.main_company` on `kod-odoo-erp` (likely, since this instance exists only for Teracorp), the payment-request cron's provider lookup (enabled-before-test, then lowest sequence) can pick this placeholder-keyed seed instead of Terakidz's real provider. **Disable it** (`state → disabled`) as part of Stage 2, and do not post real settlements through its seed journal — create or select Terakidz's genuine bank journal instead. |
| Sandbox → production switch | Two independent fields on the *Terakidz* provider, changed together at cutover (Stage 4): core's `state` (`test → enabled`) **and** `midtrans_environment` (`sandbox → production`, which is what actually swaps the Midtrans base URL upstream computes) — plus the real production `midtrans_server_key`/`midtrans_client_key`/`midtrans_merchant_id` from 1Password. Changing only one of the two leaves it either live with sandbox URLs or in `test` state hitting production URLs — neither is the intended cutover. |
| `api_base_override` | `ir.config_parameter` key `payment_midtrans_guard.api_base_override` — a guard-only escape hatch, honoured **only while `state == 'test'`**, that redirects Core API calls to an arbitrary base (used exclusively by the local fake-server E2E rig; https required except `localhost`/`127.0.0.1`). It has no effect once the provider is `enabled`, but **clear/verify-absent it explicitly before Stage 4** anyway — belt-and-suspenders against a later `test`-state rehearsal silently talking to a stale fake host. Terakidz's production provider should never carry this parameter. |

## Stage 0 — preflight

```bash
cd kodemeio-odoo
bin/deploy-preflight kod prod
```

Must say **CLEAR** (drift, orphan-installed, missing-bridge, missing-data-file
and armed-env checks, all read-only, under a minute). If it refuses, fix what
it names — never pass `all` to any release verb to work around it (repo rule).

**Verify:** the command's own PASS/FAIL summary line; no further action needed
for a CLEAR result.

## Stage 1 — install the addons

`payment_midtrans` (idtpp's own addon, code untouched — D1) and the new
Teracorp-only `payment_midtrans_guard` (depends on `sale`; currently
`18.0.1.5.1`) both need to reach `kod-odoo-erp` for the first time.

```bash
./odoo.sh release kod prod install payment_midtrans,payment_midtrans_guard --yes
```

`install` mode is for a brand-new/uninstalled module: it triggers the CI
image build (both modules must already be committed to `18.0`), waits for it,
sets `ODOO_INSTALL_MODULES`, redeploys, and cleans the transient env var up
even on failure (`./odoo.sh addon kod prod install ... --dry-run` confirms
the resolved plan: *"verify image-presence, then RPC / build+deploy"*).

**Verify:**
```bash
./odoo.sh addon kod prod info payment_midtrans_guard      # state=installed, version 18.0.1.5.1 (or later)
./odoo.sh addon kod prod info payment_midtrans            # state=installed
./odoo.sh health kod prod                                 # web/doctor/monitor all green
```

## Stage 2 — Terakidz company and provider

1. **Chart of accounts + bank journal.** Terakidz needs a real chart of
   accounts and its own bank journal before anything can settle — the
   provider's `journal_id` must point at a Terakidz bank journal, never the
   seed's `MDTRS` journal on `base.main_company`. Use the canonical company
   setup path, never a bespoke script:
   ```bash
   ./odoo.sh company kod prod validate-coa -c <terakidz_company_id> --fix
   ```
   (Odoo UI: Accounting → Configuration → Journals → New, type Bank, company
   Terakidz — this step is UI-driven; there is no dedicated CLI verb to
   create one journal.)

2. **Automatic invoicing.** `sale`'s own `_post_process` invoices the
   confirmed order once the Midtrans transaction reaches `done`, then the
   payment reconciles against that invoice — this only happens when automatic
   invoicing is on:
   ```bash
   ./odoo.sh kod prod shell call ir.config_parameter set_param '["sale.automatic_invoice", "True"]'
   ```
   (Odoo UI equivalent: Settings → Sales → Invoicing → **Automatic Invoice**.)

3. **Disable the upstream seed provider**, if Terakidz is `base.main_company`
   on this DB (check first):
   ```bash
   ./odoo.sh kod prod shell call payment.provider search_read \
     '[[["code","=","midtrans"]]]' -k '{"fields":["id","name","state","company_id","midtrans_environment"]}'
   ./odoo.sh kod prod shell call payment.provider write \
     '[[<seed_provider_id>], {"state": "disabled"}]'
   ```

4. **Create Terakidz's own provider** (Odoo UI: Accounting/Sales → Payment
   Providers → New — the credential fields are `groups="base.group_system"`,
   so this step is naturally system-admin-only; there is no `addon`/`company`
   CLI verb for it). Set:
   - `company_id` = Terakidz
   - `journal_id` = Terakidz's real bank journal (step 1)
   - `state` = `test`
   - `midtrans_environment` = `sandbox`
   - `midtrans_merchant_id` / `midtrans_client_key` / `midtrans_server_key` =
     the **sandbox** values from the 1Password item above
   - `midtrans_payment_methods` = `gopay_qris` (QRIS) or `bank_transfer` (VA;
     the bank comes from `ir.config_parameter payment_midtrans_guard.va_bank`,
     default `bca` — set it if Terakidz's VA bank is not BCA)
   - link a `payment.method` (core's `payment.payment_method_qris` xmlid for
     QRIS) — it does not need to be `active`.

5. **Company flags** (Odoo UI: Settings → Companies → Terakidz → **Midtrans**
   tab — the friendly, reviewable path; the same two fields are scriptable):
   ```bash
   ./odoo.sh kod prod shell call res.company write \
     '[[<terakidz_id>], {"midtrans_auto_request": false, "midtrans_alert_user_id": <accounting_user_id>}]'
   ```
   Leave `midtrans_auto_request` **off** for now — Stage 3 turns it on only
   after the sandbox rehearsal passes. `midtrans_alert_user_id` must be an
   internal Terakidz user with Billing or read-only accounting access
   (enforced by a field constraint); leaving it empty falls back to the
   company's first Accounting Manager.

**Verify:**
```bash
./odoo.sh kod prod shell call payment.provider search_read \
  '[[["code","=","midtrans"],["company_id","=",<terakidz_id>]]]' \
  -k '{"fields":["name","state","midtrans_environment","journal_id","midtrans_payment_methods"]}'
```
Exactly one row, `state=test`, `midtrans_environment=sandbox`, `journal_id`
on Terakidz. Confirm the seed provider (if any) now reads `state=disabled`.

## Stage 3 — sandbox rehearsal

1. Set the Midtrans **Sandbox** dashboard's notification URL (Account and
   keys table, above).
2. Turn payment requests on for Terakidz:
   ```bash
   ./odoo.sh kod prod shell call res.company write '[[<terakidz_id>], {"midtrans_auto_request": true}]'
   ```
3. Confirm one real Terakidz sale order (small amount) and let the cron pick
   it up (every 2 min) or trigger it once by hand:
   ```bash
   ./odoo.sh kod prod cron run "Midtrans: Create Payment Requests"
   ```
   ⚠️ Both the CLI `cron run` and the Odoo UI's "Run Manually" button execute
   the cron **synchronously inside that call**, and it **commits per order as
   it goes** (Task 5 fix round 1). A dropped connection or a Ctrl-C after
   order 3 of 10 leaves orders 1–3's Midtrans charges live and recorded —
   never assume a failed/interrupted "Run" did nothing.
4. Pay the resulting QRIS/VA through the real Midtrans **sandbox** simulator,
   and watch the transaction move to `done`.

**Verify:**
```bash
./odoo.sh kod prod logs errors --lines 200 --grep midtrans      # no tracebacks
./odoo.sh kod prod shell call sale.order search_read \
  '[[["id","=",<order_id>]]]' -k '{"fields":["payment_request_state","invoice_status","midtrans_qr_url"]}'
```
`payment_request_state=requested`, the invoice posts and its payment
reconciles in the **Terakidz** company (company on the invoice, the payment
and every move line) — the same P2 assertion Task 6's own HTTP e2e test
makes locally against the fake server. Do this at least once per payment
method you plan to accept (QRIS, and VA if used) before Stage 4, and **record
the result before Stage 4** (final review, "Not run"): the charge-response and
notification shapes were only ever exercised against the local fake, so this
rehearsal is the only check of them against real Midtrans, and a sandbox
pass recorded here is what Stage 4's go/no-go rests on.

## Stage 4 — production cutover

Only after Stage 3's rehearsal is clean and the production Midtrans keys
exist in 1Password.

1. Set the Midtrans **Production** dashboard's notification URL (same host,
   separate environment setting).
2. Confirm `payment_midtrans_guard.api_base_override` is absent:
   ```bash
   ./odoo.sh kod prod shell call ir.config_parameter search_read \
     '[[["key","=","payment_midtrans_guard.api_base_override"]]]'
   ```
   If a row comes back, delete it (`unlink` by id, or set an empty value and
   remove the record) — it must not survive into production.
3. Flip the provider (Odoo UI recommended for this one — the credential
   fields are system-admin-gated and best changed with eyes on the form; the
   scriptable equivalent is shown for completeness):
   ```bash
   ./odoo.sh kod prod shell call payment.provider write \
     '[[<terakidz_provider_id>], {"state": "enabled", "midtrans_environment": "production",
        "midtrans_merchant_id": "<from 1Password>",
        "midtrans_client_key": "<from 1Password>",
        "midtrans_server_key": "<from 1Password>"}]'
   ```
4. Pay one small real order end-to-end before declaring go-live.

**Verify:** same checks as Stage 3's "Verify" block, against the real
provider; additionally confirm `midtrans_environment=production`,
`state=enabled`, and that `api_base_override` is still absent
(`ir.config_parameter search_read` again).

---

## Operations

### `midtrans_auto_request` (company flag)

`res.company.midtrans_auto_request` — when on, confirming a Terakidz sale
order with a positive, unpaid total queues a payment request
(`payment_request_state=queued`); the cron creates the actual Midtrans charge
later, never inside the confirmation, so a Midtrans outage never blocks a
sale-order confirmation. Off: orders confirm normally with no Midtrans
involvement.

### Monitoring

| What | Where | Query |
|---|---|---|
| Every human alert ever raised (append-only; no "resolved" flag by design — see below) | `midtrans.guard.alert` | `./odoo.sh kod prod shell call midtrans.guard.alert search_read '[[["company_id","=",<terakidz_id>]]]' -k '{"fields":["key","reason_code","transaction_id","res_model","res_id","user_id","activity_id"],"order":"id desc","limit":50}'` |
| **Still-open** alerts | same model | filter to rows whose `activity_id` is still a live `mail.activity` — the ledger row survives forever (a refund lists earlier refunds again and must not re-alert), but core **deletes** the activity once a human marks it done, so a dangling/empty `activity_id` on an old row means it was already handled. |
| Amount mismatch | alert `reason_code` `amount_mismatch` / `settlement_after_amount_mismatch` | filter the query above with `["reason_code","like","amount_mismatch"]` |
| Already paid manually | `reason_code = already_paid_manually` | same, `["reason_code","=","already_paid_manually"]` |
| Reversal (settlement → deny) | `reason_code` `reversal` / `settlement_after_reversal` / `notification_after_reversal:*` (each carries a `\|g<N>` clear-generation suffix) | same, `["reason_code","like","reversal"]`; cross-check `payment.transaction.midtrans_reversed` |
| Failed payment requests | `sale.order.payment_request_state = 'failed'` | `./odoo.sh kod prod shell call sale.order search_read '[[["payment_request_state","=","failed"],["company_id","=",<terakidz_id>]]]' -k '{"fields":["name","payment_request_error","payment_request_attempts"]}'` |
| Raw notification/event trail | `midtrans.notification.event` (`applied`, `rejection_reason`, `payload_sha256`) | `./odoo.sh kod prod shell call midtrans.notification.event search_read '[[["applied","=",false]]]' -k '{"fields":["order_id","transaction_status","rejection_reason","source"],"order":"id desc","limit":50}'` |
| Container tracebacks | container log, not `ir_logging` | `./odoo.sh kod prod logs errors --lines 500 --grep midtrans` |

Odoo's own Activities view (Discuss → Activities, or the bell icon on an
`account.payment`/`account.move`/`sale.order` record) is the normal place a
human actually **works** an alert — the queries above are for building a
dashboard or a scheduled digest, not a replacement for that view.

**Three crons matter on this instance.** The guard adds only the middle one;
the other two come from `payment_midtrans` itself (upstream, unmodified):

| Cron | Cadence | What it does to a Terakidz payment |
|---|---|---|
| "Midtrans: Check Pending Transactions" | every 5 min | Polls every `pending` Midtrans transaction by GET status — the guard's payment requests included. Its answers go through the guard, so a settlement found this way is posted (with a `reopened_after_expiry` alert when the transaction had expired). Transactions held as `already_paid_manually` are deliberately skipped (`midtrans_guard_paid_elsewhere`); the manual **Check status** button still queries them. |
| "Midtrans: Create Payment Requests" (guard) | every 2 min | Claims `queued`/`retry` orders and creates the charge; see the alerts above. |
| "Midtrans: Expire Old Transactions" | hourly | Cancels expired transactions **in Odoo**. After it runs the order stays `requested` with a dead QR/VA and **nothing re-requests or alerts by itself** — press **Request payment** on the order, or let the customer pay another way. |

### Resolving each alert (full text lives on the activity itself)

| Alert | Resolution |
|---|---|
| `reversal` (settlement → deny) | Confirm in the Midtrans dashboard, then unwind in Odoo: unreconcile the payment from the invoice and cancel the payment; follow up with the customer. |
| `settlement_after_reversal` / `notification_after_reversal:*` | Ask Midtrans which status is final. If the money really arrived, an Accounting Manager clears the reversal flag (below) and registers the payment manually. |
| `settlement_after_deny` / `refund_before_settlement:*` / `inconsistent_settlement` / `terminal_state_conflict` | Raised only when the guard's own GET-status query to Midtrans could not be applied either. Check the real status in the Midtrans dashboard; if paid, invoice the order first if none exists, then Register Payment on the invoice (Midtrans journal). |
| `status_query_failed` / `status_answer_apply_failed` | The guard could not ask Midtrans, or Midtrans's answer failed to apply and was rolled back — **nothing was posted**. **Do not re-press "Check status."** That only feeds a fresh GET into the same guard; if Midtrans's own answer has since changed (it now reports the settlement), pressing it **is** the recovery path and applies that answer exactly once — but pressing it again while the answer is unchanged posts and changes nothing. Follow the resolution above instead (dashboard check, manual registration if paid) and check the provider's server key / Midtrans's own status page. |
| `amount_mismatch` / `settlement_after_amount_mismatch` | Compare the Midtrans amount with the order. Right amount: invoice first if needed, then register the payment. Wrong amount: refund the difference through the Midtrans dashboard (always human, see below) and record it. |
| **`already_paid_manually`** (incl. **partial payment**) | Midtrans settled, but Odoo already shows a payment. Two of it: refund one through the Midtrans dashboard. Wrong manual entry: cancel it and register the Midtrans one. **Partial payment on the invoice: register the Midtrans payment, then refund the excess through the Midtrans dashboard.** (Edge case: a partial credit note can also make the invoice read `partial` — a human decides then too.) The guard never posts this on its own. |
| `reopened_after_expiry` | Informational: an expired transaction was settled because Midtrans's authenticated status said so — the payment is posted. Confirm it in the dashboard; if the linked order was cancelled (the note says so), reinstate it or refund the customer. |
| `payment_request_failed` (on the sale order) | Read `payment_request_error`: fix the server key (401) or the order/customer data (4xx), then press **Request payment** (`sale.order.action_midtrans_request_payment()`), or take the payment another way. |
| `charge_amount_rounded` (on the sale order) | Informational, and **not a defect**: the order total has sub-rupiah cents and Midtrans cannot process decimals in IDR, so the payment request is for the whole-rupiah amount actually charged (e.g. 150000 of 150000.50) — the payment that posts matches the money received, and the remainder stays on the order. When the customer has paid, settle the remainder by hand (write it off or issue a credit note), or round the order's total and press **Request payment** again. |
| `refund_chargeback:*` | Record it in Odoo: a credit note for the refunded amount plus the outgoing refund payment (chargeback: only after reviewing it with Midtrans/the issuer). |

Re-sending ("resend notification") the same Midtrans notification from the
dashboard is **never the fix** for any of the above — an identical body is
just answered `duplicate` and changes nothing.

**One case raises no alert at all** (guard limitation, final review M3):
if an accountant posts the invoice by hand **before** the customer pays, and
the Midtrans settlement then arrives, the guard applies it and core posts the
payment — but `sale` has no invoice left to create, so the posted payment can
be left **unreconciled** against that already-posted invoice, which therefore
still shows as unpaid. The guard's `already_paid_manually` hold only
recognises invoices already in `paid`/`in_payment`/`partial`. So: after a
Midtrans settlement on an order whose invoice was posted by hand beforehand,
**check the invoice is reconciled** (Register Payment / manual reconciliation
on the invoice) and that dunning will not chase a customer who has paid.

**Clearing a sticky reversal** — Odoo UI **only**, by design: the "Clear
Midtrans Reversal Flag" server action on the `payment.transaction` form
(Action menu; Accounting Managers only) opens a wizard that requires a
mandatory reason, re-checks the group, locks the row, clears
`midtrans_reversed`, and logs the reason on the payment's chatter. Do not
`shell call` `payment.transaction._midtrans_guard_clear_reversal()` directly —
that bypasses the group gate the server action enforces at the click, which
is the entire point of routing this through a wizard rather than a plain
field write.

**A repeated `deny` right after a clear is silent — by design** (Task 3
N-m2, parked). Clearing the flag does not move the stored lifecycle anchor,
which still reads `deny`; a deny is only treated as a reversal when the anchor
is at settlement precedence or later, so a deny arriving after the clear
without a new settlement in between is neither re-flagged nor alerted. Only
a settlement that is then denied again raises a new `reversal` alert. If you
believe the money really was reversed after a clear, clear the flag **only
once you have confirmed it in the Midtrans dashboard**, and record the
unwind by hand.

### Stale QR/VA after a re-confirm

Re-confirming an eligible order (including cancel → draft → confirm) cancels
its open Midtrans transaction internally and queues a **new** charge with a
new QR/VA — but the **old** charge is still payable at Midtrans until it
naturally expires (`midtrans_snap_expiry`, default 24h). If a customer still
has the old QR image or VA number (a screenshot, a printed slip), paying it
is not silently lost — the guard's `terminal_state_conflict` path queries
Midtrans and re-opens it with a `reopened_after_expiry`-style alert — but it
is confusing and creates exactly the kind of ambiguity a human then has to
resolve. **Support must tell a customer whose order was re-confirmed to use
the current QR/VA shown on the order, never a previously saved one.**

**Cancelling a confirmed order is the same shape, without the guard's help.**
Nothing in Odoo calls Midtrans when an order is cancelled, so its QR/VA stays
payable at Midtrans until it expires. If the customer pays it anyway, the
guard posts an `account.payment` for the money, but `sale` does not invoice a
cancelled order — so nothing is reconciled and no alert is raised. **Cancel
the payment request in the Midtrans dashboard as part of any order
cancellation**, and if a payment does arrive for a cancelled order, register
it by hand (reinstate the order, or refund through the Midtrans dashboard and
record it).

**Never edit a confirmed order's lines while a payment request is live.** The
transaction amount is frozen when the charge is created (that is what makes
the amount check exact), so a later line change leaves the live QR/VA for the
old amount and the settlement will simply be for less than the new total —
with no alert, because the guard compares against what Midtrans is actually
asked for. Change the order first, then re-confirm/re-request so a new charge
matches the new total.

### Refunds are always human

PAY1 does not implement refund execution (spec §5, out of scope) — a human
issues every refund directly in the Midtrans dashboard, then records it in
Odoo (credit note + outgoing refund payment, per the `refund_chargeback:*`
row above). Per spec D4, a human-initiated refund is a **financial-risk
operation with a distinct approver** — the same governance shape as
`mcp_operation_binding`'s policy binding
(`ops/runbooks/teracorp-odoo-rollout.md`): the person who reads the alert and
decides a refund is owed is never the same person who logs into the Midtrans
dashboard and presses refund, and both names go on the alert's chatter note.
Building that as an enforced MCP gate (rather than a procedural rule) is
future work, not part of this rollout.

### i18n

Every guard-facing string (alert labels, activity notes, resolution text) is
English-only today — `id_ID` was explicitly deferred (Task 3 fix round 1,
M5) rather than blocking rollout. Tracked as a follow-up, not a defect to fix
here.

## Rollback — kill switch

Two moves, both required (either alone is a partial kill):

```bash
./odoo.sh kod prod shell call res.company write '[[<terakidz_id>], {"midtrans_auto_request": false}]'
./odoo.sh kod prod shell call payment.provider write '[[<terakidz_provider_id>], {"state": "disabled"}]'
```

- Turning off `midtrans_auto_request` alone only stops **new** sale-order
  confirmations from queueing a payment request — it does nothing about
  orders already `queued`/`retry`, which the cron will keep trying.
- Disabling the provider stops the cron from creating any further charge for
  those orders too (the eligibility check requires an `enabled`/`test`
  provider) — **and** it makes the webhook controller refuse every incoming
  notification for that provider outright, the same way it refuses a bad
  signature (guard CLAUDE.md, "N1"): logged, no writes, 200 back to Midtrans.
  Any Midtrans payment that settles **while the provider is disabled** will
  not be recorded automatically — check the Midtrans dashboard and register
  it manually for any order left mid-flight at the moment of rollback.
- **Midtrans does NOT redeliver anything the webhook already answered.** This
  endpoint is a `type="json"` route and always answers HTTP 200, and
  Midtrans's retry schedule (2/10/30/90/210 min) is driven by non-2xx replies
  and timeouts only — so a notification that arrives while the provider is
  disabled is refused, logged, answered 200 and **never comes back** (spec
  deviation D-5; guard CLAUDE.md, "N5"). Re-enabling the provider does **not**
  replay it. Do not rely on Midtrans's redelivery for any part of this
  rollback.
- **Recovery after a disabled window** — the same rule as the held-ambiguity
  path (R10(b)): ask Midtrans, never wait for Midtrans to send it again.
  1. **Anything still `pending` in Odoo:** let the upstream cron "Midtrans:
     Check Pending Transactions" run, or press **Check status** on the
     transaction. Both query Midtrans by GET and feed the authenticated answer
     through the guard, which applies it exactly once (a settlement found this
     way is posted, with an alert). Find the candidates with:
     ```bash
     ./odoo.sh kod prod shell call payment.transaction search_count \
       '[[["provider_id","=",<terakidz_provider_id>],["state","=","pending"]]]'
     ```
  2. **Everything else** — anything already `done`/`cancel`/`error`, or not in
     Odoo at all: sweep the Midtrans dashboard for transactions settled inside
     the window, compare them against Odoo, and register any missing payment
     by hand (`already_paid_manually` is what protects this sweep: if a human
     already registered the payment, a later notification for it is held and
     alerted instead of posted a second time).

**Verify:**
```bash
./odoo.sh kod prod shell call payment.provider search_read \
  '[[["id","=",<terakidz_provider_id>]]]' -k '{"fields":["state"]}'   # disabled
./odoo.sh kod prod shell call sale.order search_count \
  '[[["company_id","=",<terakidz_id>],["payment_request_state","in",["queued","retry"]]]]'
```
Confirm no order is silently re-queuing itself; watch `ir.cron` history for
the payment-request cron to confirm it stops finding claimable orders:
```bash
./odoo.sh kod prod cron history "Midtrans: Create Payment Requests"
```

Uninstalling either addon is deliberately **not** part of this kill switch —
`state=disabled` + the flag off is reversible in seconds and loses no data;
uninstalling drops the guard's own audit trail (`midtrans.notification.event`,
`midtrans.guard.alert`) and is not necessary to stop the flow.
