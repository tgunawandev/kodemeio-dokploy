# Teracorp CW1 — Meta / WhatsApp Cloud setup (M2/M3)

**Scope:** founder-only steps in Meta Business Manager and WhatsApp Manager
to get a real WhatsApp number talking to Chatwoot. Nothing here can be done
by Claude — Meta requires a verified business, a real phone number and a
human clicking through approval flows. This runbook is the exact sequence
and the exact values the rollout (`teracorp-cw1-rollout.md`, M6) needs from
it.

- Design: `2026-09-26-teracorp-cw1-chatwoot-design.md` §8 (M2, M3), D9-D11
- Verified facts: Task 0 report (`.superpowers/sdd/2026-09-26-teracorp-cw1-chatwoot/task-0-report.md`),
  §2.2-2.4, §2.7 — read before touching WhatsApp Manager if anything below
  looks stale; those findings are pinned to Chatwoot `v4.18.0` specifically.

## M2 — Business Manager, WABA, number, System User token, app secret

1. **Business verification.** Meta Business Manager → Business Settings →
   verify the business (document upload, may take days). Nothing else in
   this runbook can complete before this is approved.
2. **WhatsApp Business Account (WABA).** Create one under the verified
   business, associated with Terakidz.
3. **Dedicated phone number.** Must be a number **not already registered on
   the regular WhatsApp app or WhatsApp Business app** — Meta will refuse to
   migrate a number that's actively used elsewhere without a separate
   migration flow. Get a fresh number for this if Terakidz doesn't already
   have one set aside.
4. **Display name.** Submit for approval in WhatsApp Manager (business name
   shown to customers). Approval can take 1-3 business days; do not block
   the rest of the rollout on it — the number can send/receive before the
   display name is approved, it just shows the raw number instead.
5. **System User + permanent access token.** Business Settings → Users →
   System Users → create one scoped to this WABA. Required permissions
   (scopes): `whatsapp_business_messaging`, `whatsapp_business_management`.
   Generate a **permanent** token (not a short-lived user token) — this is
   the value Chatwoot's WhatsApp Cloud channel config calls the API/access
   token. Store it in 1Password immediately; it is shown once.
6. **App secret.** WhatsApp Manager → your app → Settings → Basic → App
   Secret. This is the value Chatwoot uses to verify
   `X-Hub-Signature-256` on inbound webhooks (Task 0 §2.2: Chatwoot
   **skips signature verification entirely** for a `whatsapp_cloud` channel
   with no app secret configured — this value is not optional, get it
   before connecting the inbox, not after).

   **It is also an environment variable, `WHATSAPP_APP_SECRET`, and the
   production compose REFUSES TO START without it** (`:?` — see
   `kodemeio-chatwoot/docker-compose.prod.yml`). Two places, one value:
   Chatwoot reads the env var as the global fallback for every WhatsApp
   channel, and the inbox's own `provider_config.app_secret` (set when the
   inbox is created) as the channel-specific one. Set the env var from
   1Password in Dokploy's env store as part of M6; a stack that boots
   without it would accept a **forged** webhook as genuine, which is exactly
   what CW-11 tests against locally. Never paste the value into this runbook,
   a ticket or a chat — name only.

   *Local equivalent:* `scripts/setup-local.sh` writes
   `WHATSAPP_APP_SECRET=test-app-secret-not-real` into the gitignored `.env`
   (the local compose is fail-closed the same way).
7. **Payment method on the WABA.** Required before Meta will bill/deliver
   messages at any real volume — add a payment method in WhatsApp Manager's
   billing settings.

## M3 — Message template

Only ONE template is needed for CW1 launch:

| Name | Category | Language | Purpose |
|---|---|---|---|
| `terakidz_payment_ready` | **Utility** | `id` (Indonesian) — add `en` too if staff need it | Tells a customer their payment instructions are ready, **without the payment code itself** (spec D10: the template carries the order ref + amount only — "reply to get your payment code" — so a template-param bug can never leak a payment code through this channel) |

Submit it in WhatsApp Manager → Message Templates. Utility category (not
Marketing) matters for cost: utility templates are free inside the 24h
customer-service window and charged (at the utility rate, not the marketing
rate) outside it — see D11's per-message pricing note below. Approval is
usually fast (minutes to hours) for a plain utility template with no
suspicious content, but **do not assume same-day** — submit this well before
the target go-live date.

Suggested body (founder/counsel wording — this is a starting point, not
final copy):

```
Pesanan {{1}} Anda sudah siap dibayar. Total: {{2}}.
Balas pesan ini untuk mendapatkan kode pembayaran Anda.
```

No customer name in the template (spec D9: no display name goes to the
model or into a template param) — `{{1}}` is the order ref, `{{2}}` is the
amount. Confirm the FINAL param count and order against
`kido_chat/payments.py`'s `template_params` call (Task 7) before
submitting — a template approved with a different param count than the
code sends will fail silently at send time.

## Webhook configuration (do this during rollout, M6 — not before Chatwoot exists)

Once Chatwoot is deployed (`teracorp-cw1-rollout.md` step 4) and the
WhatsApp Cloud inbox is created in Chatwoot:

- **Webhook URL**: `https://<chatwoot-domain>/webhooks/whatsapp/<phone_number_id>`
  (Chatwoot generates the exact path when the inbox is created — copy it
  from there, do not hand-construct it).
- **Verify token**: whatever Chatwoot's inbox setup generates/asks for —
  paste it into WhatsApp Manager's webhook config's `hub.verify_token`
  field. Meta will send a `GET` with `hub.mode=subscribe` and
  `hub.verify_token` to confirm before it starts delivering events.
- **Subscribe to fields**: `messages` at minimum (Task 0 §2.4 also names
  `smb_message_echoes` and optionally `calls` as fields Chatwoot's own
  webhook-subscribe call requests — accept Chatwoot's default subscription
  set rather than hand-picking a narrower one, so nothing Chatwoot expects
  silently stops arriving).

## Rate card (D12 — read before go-live, not before)

Per-message pricing since 2025-07-01: marketing templates are charged,
utility templates are free **inside** an open 24h customer-service window
and charged **outside** it, authentication templates are charged. **Read
the current Indonesia rate card** (published as a CSV/PDF in WhatsApp
Manager's pricing section, not verified in this design) before enabling
`KIDO_ENABLED=1` for real traffic — this determines Terakidz's per-customer
cost for anything that falls outside the free window.

## What this runbook does NOT cover

- Chatwoot-side inbox/agent-bot creation — `teracorp-cw1-rollout.md` (M6).
- Consent wording / privacy notice — `teracorp-cw1-privacy.md` (M5).
- Kill switches, deploy order, rollback — `teracorp-cw1-rollout.md`.

## Rollback

- **Wrong/compromised app secret or System User token**: rotate it in
  WhatsApp Manager / Business Settings, update the value in Dokploy's env
  store (`WHATSAPP_*` — set on the Chatwoot inbox via its own settings UI,
  not this compose's env), no redeploy of this compose needed.
- **Number needs to stop receiving entirely**: unsubscribe the webhook in
  WhatsApp Manager, or disconnect the inbox in Chatwoot (Settings → Inboxes
  → WhatsApp Cloud → disconnect). This is the same action as kill switch #3
  in `teracorp-cw1-rollout.md`.
- **Template misuse**: templates can be paused/deleted in WhatsApp Manager
  independent of anything on this stack.
