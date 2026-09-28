# App entitlements (ENT): one payment path for every paid app

This runbook takes a paid app (for example Terakidz, Terafin or Terakon Studio) from "built
locally" to "receives entitlements in production". Founder decision TB-D1: **Odoo PAY1
(Midtrans) is the only payment path**. An app never talks to Midtrans. It opens a signed
checkout in Odoo and receives signed `active`/`revoked` events back.

Scope is the **kodeme.io estate only**. Nothing here touches idtpp.

| Piece | Where |
|---|---|
| Contracts (schemas, header names, semantics, test vector, recorded request) | kodemeio-dokploy `contracts/entitlements/`, `contracts/examples/entitlements/` |
| Odoo addon (checkout route, plans, paid/refund seams, dispatcher) | kodemeio-odoo `src/private/integrations/app_entitlement/` (read its `CLAUDE.md`), bundle `install/private-app-entitlement.yaml` |
| Supabase module (table, `has_entitlement()`, `entitlement-webhook`) | kodemeio-supabase `modules/billing/app_entitlements/` (read its `README.md`) |
| Payments | PAY1: `ops/runbooks/teracorp-midtrans-rollout.md` must be complete for the app's company first |

Names in code are generic (`app_entitlement`, `X-Webhook-*`). App and brand names appear only in
records and secrets.

## Operational gates (all open; nothing below has been applied)

1. **R2 seller of record.** The company that sells each app is decided and its legal entity is
   `active`. The optional check sets the Odoo system parameter
   `app_entitlement.seller_resolver_model` to the model that exposes
   `resolve_seller(brand, market)` (today the R2 entity addon's brand-seller model), and sets
   `seller_brand` on each plan. With the parameter unset there is no check. The parameter is the
   only place that names the R2 module, so renaming that module needs only a parameter change.
2. **PAY1 live for that company.** A Midtrans provider, `midtrans_auto_request`,
   `sale.automatic_invoice`, and the payment-request cron, per the PAY1 runbook.
3. **A production Supabase project for the app** (TB3/TB8/TB12), with the module installed and
   its RLS matrix green.
4. **Secrets provisioned** (below), stored in 1Password and never in git.
5. **Founder drill** (below), passed on sandbox.
6. **Delivery cron activated.** "App Entitlement: Deliver Events" ships inactive.

## Stage 1 — install (Odoo)

```bash
cd kodemeio-odoo
bin/deploy-preflight kod prod                       # must say CLEAR
# Add private-app-entitlement:core to the kod instance's ODOO_INSTALL_BUNDLE, then install
# app_entitlement through the normal release path (odoo-deployment skill). Never `all`.
```

**Verify:** the module is installed. `fastapi.endpoint` "App Entitlement Checkout" exists at
`/app-entitlement` with the **public** user. The cron exists and is **inactive**. Sales →
Configuration → App Entitlements shows empty Apps and Plans.

## Stage 2 — secrets and per-app configuration

Each app has **two** secrets, one per direction. They must never be equal, and each must be at
least 32 bytes:

| Secret | Held by | 1Password item (create when provisioning) |
|---|---|---|
| checkout secret (app → Odoo) | Odoo `app.entitlement.client.checkout_secret` and the app backend (server action or edge function env) | `app-entitlement: <app> checkout` |
| entitlement secret (Odoo → app) | Odoo `app.entitlement.client.entitlement_secret` and the Supabase function secret `APP_ENTITLEMENT_SECRET` | `app-entitlement: <app> entitlement` |

Generate each with `openssl rand -hex 32`, straight into 1Password. Never paste one into this
file, a commit, a ticket or a chat.

In Odoo (Sales → Configuration → App Entitlements → Apps), per app:
- `app` is the contract's `app` value (`contracts/entitlements/app_checkout.v1.schema.json`
  enum). Adding a new app means extending that enum first, which is a contract change.
- `company` is the seller of record's company.
- `webhook_url` is `https://<supabase project host>/functions/v1/entitlement-webhook`. Only
  HTTPS is accepted; plain HTTP is allowed only for localhost.
- The two secrets. They are visible to Settings/system users only.

Plans (same menu), per app: `plan_code` (for example `premium-30d`), a **service** product at
the plan price in the same company, `period_days` (30 or 365), and optionally `seller_brand`.

In Supabase (per app project):
```bash
kctl-supa -p <project> module install billing.app_entitlements   # then commit the migration
# supabase/config.toml: [functions.entitlement-webhook] verify_jwt = false
```
The function secrets `APP_ENTITLEMENT_APP` and `APP_ENTITLEMENT_SECRET` are added as names only
(no values) to `projects/<project>/env/.env.cloud.example`, which is the secrets contract. Their
values go into the repository's CI secrets, and the deploy workflow's
`scripts/functions-secrets.sh` sets them, refusing by name if one is missing. Use the kctl-supa
front door and CI, not raw curl.

**Verify:** a GET to the function answers `405`. Without its secrets it answers `500
not_configured`, which proves the secrets are what gate it.

## Stage 3 — the app backend

The app's server side (a Next server action or a Supabase edge function, never the browser)
signs `app_checkout.v1` with the checkout secret. See `contracts/entitlements/README.md`. It
POSTs the request to `https://erp.kodeme.io/app-entitlement/checkout`, which sits behind
Dokploy's Traefik and is never an exposed port. It shows the QRIS/VA from the answer. **To poll,
re-send the same body with a fresh timestamp.** The answer's `status` moves from
`awaiting_payment_request` to `awaiting_payment` to `paid`.

## Stage 4 — founder drill (sandbox, one app)

1. Check out a synthetic account (`.test` data only). Expect `awaiting_payment_request`. After
   the PAY1 cron runs, expect `awaiting_payment` with a QR string.
2. Pay in the Midtrans **sandbox** simulator. Expect the invoice to be paid and one `active` event
   in Odoo (Events menu).
3. Run the delivery cron once by hand. The event should become `delivered`. In Supabase,
   `select public.has_entitlement('<app>', 'premium')` as that user returns true.
4. Replay: send the same notification from the simulator again. Expect no new event and no
   second payment (PAY1 P3).
5. Refund: post a full credit note for the invoice. Expect one `revoked` event. After delivery,
   `has_entitlement` returns false.
6. Break the webhook secret on purpose (Supabase side). The next event gets `401`, backs off,
   and reaches `dead` after 8 attempts. Restore the secret, press **Re-queue**, and confirm the
   event is delivered.

Record the drill's event ids (never account refs) in the ops log.

## Stage 5 — activate delivery

Activate "App Entitlement: Deliver Events" (every 2 minutes). Watch the Events menu for `dead`
rows. A dead row always names its cause in `last_error` (`http_401` means a wrong secret,
`not_configured` means a missing URL or secret, a transport error class means the network).

## Failure modes

| Symptom | Meaning | Action |
|---|---|---|
| Checkout `401 bad_signature` | The app signs with the wrong secret, or modifies the body after signing | Check the app's checkout secret. It must sign the exact bytes it sends. |
| Checkout `401 stale_timestamp` | Clock skew over 300 s | Fix NTP on the caller. |
| Checkout `409 idempotency_conflict` | The same key was reused with a different body | The app must mint a new key per attempt. |
| Checkout `409 checkout_in_progress` | Two identical requests raced | Retry the same request; it answers as a replay. |
| Checkout `422 unknown_plan` | The plan code is not configured for that app and company | Create the plan, or fix the app's plan code. |
| Checkout `422 order_refused` | Confirmation was refused (for example the R2 entity is not active) | Resolve the seller gate. |
| Event `dead`, `http_401` | Odoo and Supabase hold different entitlement secrets | Re-provision both from 1Password, then Re-queue. |
| Paid, but no event | The order was not opened by a checkout, or the refund came first | Check that the order has an App Checkout; check the Events menu. |
| Refund of one line while a renewal exists | Fail-closed: the consumer ends the whole plan row | Any later paid line re-activates it (a fresh `active`). To restore the remaining window sooner, unreconcile and re-reconcile the renewal's payment in Odoo: that opens a new generation. |

## Rollback

Deactivate the delivery cron, which stops outbound events. Archive the app client, which makes
checkouts answer `401 unknown_source`. Paid orders and invoices are ordinary Odoo documents and
stay as they are. Never uninstall the addon while events or checkouts exist; follow the addon
retirement rule (`installable: False` first).
