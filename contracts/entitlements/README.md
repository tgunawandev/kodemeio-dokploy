# Entitlement contracts (Teracorp Track B, slice ENT)

One payment spine for every paid app (founder decision TB-D1): Odoo PAY1 (Midtrans) is the only
payment path. An app never talks to Midtrans; it asks Odoo for a checkout and receives an
entitlement event back.

```
app backend ──app_checkout.v1──▶ Odoo POST /teracorp/app/checkout ──▶ sale.order (confirm) ──▶ PAY1 QRIS/VA ref
                                                                            │ PAY1 settles
app entitlement-webhook ◀──entitlement.v1── Odoo dispatcher cron ◀── outbox row per paid line
```

| Contract | Direction | `X-Teracorp-Source` | `X-Teracorp-Event-Id` |
|---|---|---|---|
| `app_checkout.v1` | app → Odoo | the `app` value (`terakidz`, `terafin`, `terakon-studio`) | the body's `idempotency_key` |
| `entitlement.v1` | Odoo → app | `teracorp-odoo` | the body's `event_id` |

## Signing (mirrors `kodemeio-hatchet/workers/order_intake`)

Every request carries four headers:

- `X-Teracorp-Source` — per the table above.
- `X-Teracorp-Timestamp` — unix seconds, ASCII digits only, at most 12 characters. The verifier
  refuses anything more than **300 s** from its clock (`stale_timestamp`).
- `X-Teracorp-Event-Id` — per the table above; must equal the id inside the body, or the verifier
  refuses (`event_id_mismatch`).
- `X-Teracorp-Signature` — `sha256=<lowercase hex>` of
  `HMAC-SHA256(secret, f"{source}.{timestamp}.{event_id}.".encode() + raw_body)`.
  `raw_body` is the exact request bytes; compare in constant time.

Secrets are **per app and per direction**: `checkout` (app → Odoo) and `entitlement`
(Odoo → app) never share a value, so a captured request cannot be reflected back. Each side
refuses to start with a secret shorter than 32 bytes. Secrets live in Odoo `ir.config_parameter`
/ Supabase function secrets — never in git.

`signing.v1.vector.json` (under `examples/entitlements/`) is a fixed test vector with a
test-only key. Every implementation (Odoo, Supabase Deno, Next server action) must reproduce
its signature byte for byte.

## Semantics

- **Checkout idempotency:** the same `(app, idempotency_key)` with the same body returns the
  original checkout; with a different body it is refused (`idempotency_conflict`).
- **Opaque partner:** Odoo stores only `app:<sha256(app + ":" + app_account_ref)>` on a partner
  with no name, email or phone taken from the caller.
- **Company:** the plan's company (the brand's seller) owns the order; a plan is resolved only
  inside its own company.
- **One event per paid line:** Odoo writes one `active` outbox row per paid invoice line; a
  replayed settlement writes nothing new. Renewal windows stack: `valid_from` is the later of
  now and the account's current `valid_until` for that plan.
- **Revocation:** a refund or cancellation writes one `revoked` event for that line; the consumer
  ends the `(app, app_account_ref, plan_code)` entitlement (fail-closed: a concurrent renewal is
  re-granted by the founder, never silently kept).
- **Consumer rules:** claim `event_id` once; `active` sets `valid_until = greatest(current, new)`
  (monotonic); an event whose `issued_at` is older than the row's last `revoked` is ignored.
- **Delivery:** at most once per event from Odoo's side (each event is marked delivered on 2xx and
  never re-sent); failures retry with exponential backoff and stop at a named `dead` state.

## Examples

`examples/entitlements/*.valid.json` validate; `*.invalid-<reason>.json` fail for the named
reason (`deploys/tests/test_contracts_entitlements.py`). `entitlement.v1.recorded.json` is a
payload produced by the Odoo dispatcher test and replayed by the Supabase function test (T4).
