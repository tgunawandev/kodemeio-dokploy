# Entitlement contracts (app entitlement bridge, slice ENT)

One payment spine for every paid app (founder decision TB-D1): Odoo PAY1 (Midtrans) is the only
payment path. An app never talks to Midtrans. It asks Odoo for a checkout and receives an
entitlement event back.

```
app backend ──app_checkout.v1──▶ Odoo POST /app-entitlement/checkout ──▶ sale.order (confirm) ──▶ PAY1 QRIS/VA ref
                                                                              │ PAY1 settles
app entitlement-webhook ◀──entitlement.v1── Odoo dispatcher cron ◀── outbox row per paid line
```

Names are generic on purpose: the Odoo addon is `app_entitlement`, and the Supabase module is
`modules/billing/app_entitlements`. Brand and app names live only in data: the `app` enum here,
Odoo plan records, and per-app secrets and URLs. No code branches on them.

| Contract | Direction | `X-Webhook-Source` | `X-Webhook-Event-Id` |
|---|---|---|---|
| `app_checkout.v1` | app → Odoo | the body's `app` value | the body's `idempotency_key` |
| `entitlement.v1` | Odoo → app | `odoo` | the body's `event_id` |

## Signing (the `order_intake` scheme, with generic header names)

Every request carries four headers:

- `X-Webhook-Source`: the value from the table above.
- `X-Webhook-Timestamp`: unix seconds, ASCII digits only, at most 12 characters. The verifier
  refuses anything more than **300 s** from its clock (`stale_timestamp`).
- `X-Webhook-Event-Id`: the value from the table above. It must equal the id inside the body, or
  the verifier refuses (`event_id_mismatch`).
- `X-Webhook-Signature`: `sha256=<lowercase hex>` of
  `HMAC-SHA256(secret, f"{source}.{timestamp}.{event_id}.".encode() + raw_body)`.
  `raw_body` is the exact request bytes. Compare in constant time.

Each app has its own secrets, one per direction. The `checkout` secret (app → Odoo) and the
`entitlement` secret (Odoo → app) never share a value, so a captured request cannot be reflected
back. Each side refuses to run with a secret shorter than 32 bytes. Secrets are stored in Odoo
plan configuration and in Supabase function secrets, never in git.

`examples/entitlements/signing.v1.vector.json` is a fixed test vector with a test-only key. Every
implementation (Odoo, Supabase Deno, Next server action) must reproduce its signature byte for
byte.

## Semantics

- **Checkout idempotency.** A retry with the same `(app, idempotency_key)` and the same body
  returns the original checkout, including the PAY1 payment instructions once the payment request
  cron has created them. A retry therefore works as a status poll. The same key with a different
  body is refused (`idempotency_conflict`).
- **Opaque partner.** Odoo stores only `app:<sha256(app + ":" + app_account_ref)>`, on a partner
  with no name, email or phone taken from the caller.
- **Company.** The plan's company (the brand's seller) owns the order. A plan is resolved only
  inside its own company.
- **One event per paid line.** Odoo writes one `active` outbox row per paid invoice line. A
  replayed settlement writes nothing new. Renewal windows stack: `valid_from` is the later of now
  and the account's current `valid_until` for that plan.
- **Revocation.** A full refund, or an invoice that leaves the paid state, writes one `revoked`
  event for that line. The consumer then ends the `(app, app_account_ref, plan_code)`
  entitlement. This fails closed: if a concurrent renewal exists, the founder re-grants it; it is
  never kept silently.
- **Consumer rules.** Claim `event_id` once. For `active`, set
  `valid_until = greatest(current, new)`, so the window only moves forward. Ignore any event whose
  `issued_at` is older than the row's last `revoked`.
- **Reading an entitlement (app side).** `has_entitlement(p_app, p_plan_prefix, p_account_ref)`
  is true only while the row is `active` and `valid_from <= now < valid_until`, and only when
  `plan_code` equals the prefix or starts with `prefix-`. The caller must be allowed to see the
  account: either it is the caller's own id, or the project's `ent_account_visible` hook admits
  them. Anyone else gets `false`.
- **Delivery.** Odoo sends each event at most once: an event is marked delivered on a 2xx and is
  never re-sent. Failures retry with exponential backoff and stop at a named `dead` state.

## Examples

`examples/entitlements/*.valid.json` validate. Each `*.invalid-<reason>.json` fails for the
reason in its name (`deploys/tests/test_contracts_entitlements.py`).
`entitlement.v1.recorded.json` is a payload produced by the Odoo dispatcher test and replayed by
the Supabase function test (T4).
