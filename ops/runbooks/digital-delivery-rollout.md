# Runbook — Digital delivery on kodeme.io (founder-gated)

Takes R6 / DIG1 from **built and tested locally** to one founder-approved paid digital
delivery on the kodeme.io estate: a released factory artefact is attached to a digital product,
a paid order grants one replay-safe entitlement, the buyer receives the bytes through a scoped
expiring capability, and a full refund revokes the link.

This is a founder runbook. Read-only checks are marked **R**; production changes are **M**.
Nothing in this document has been applied by the implementation slice. Every target is kodeme.io
(`kod` or `kod-desk`), never idtpp. Do not run a production command until the seller-of-record
decision in M0 is recorded.

Slice of record: `kodemeio-docs/superpowers/specs/2026-09-27-teracorp-digital-delivery-design.md`
and `…/plans/2026-09-27-teracorp-digital-delivery.md`. Roadmap row: **R6**.

## What is built locally

| Fact | Evidence |
|---|---|
| Released-render binding, scoped company lookup, paid entitlement, hash re-read, hashed capability link, delivery ledger, sale guards, and refund revoke are implemented | `kodemeio-odoo` `factory_digital` |
| The module suite is green | `TEST_DB=odoo_test_factory_r6 ./odoo.sh dev ut factory_digital`: 70 tests, 0 failed; 56 post-tests, 0 failed/errors |
| The link is an opaque random capability; only its SHA-256 is stored | `factory_digital/models/factory_digital_link.py` |
| Bundle metadata and catalogs are present | `bin/validate-bundles install`: 0 errors; `bin/lint-addon-i18n src/private/factory/factory_digital`: exit 0 |
| No production state, DNS, secret, payment account, or deployment was changed | implementation boundary |

Local evidence is not live evidence. Production is **not** considered operational until the
founder completes the drills below and records the result in the R6 results document.

## M0 — Decide the instance and seller of record

Record these choices before installing anything:

```text
Target instance:       [ ] kod (erp.kodeme.io)   [ ] kod-desk (desk.kodeme.io)
Seller of record:      [ ] company A             [ ] company B
Digital product:       [ ] PDF/printable pack    [ ] other released artefact
Refund policy owner:   [ ] Odoo company          [ ] merchant/payment provider
```

R2 is still open in the program roadmap. R6 refuses a cross-company checkout, but it does not
choose the legal entity, journal, tax setup, payment account, or merchant-of-record contract.
Until R2 is decided, run only the local acceptance suite and the bundle dry run.

### Attachment decision (R2 vs attachment)

The R6 delivery attachment is an Odoo `ir.attachment` belonging to the released render. It is
the **delivery source**, not the seller-of-record decision and not a public object-store URL.
Keep the attachment path when:

- the released printable is already in the Odoo filestore and its recorded SHA-256 is the source
  of truth;
- the chosen Odoo company owns the product, sale order, invoice, entitlement, and refund event;
- the filestore backup/restore policy covers the attachment before real money moves.

Do not introduce a second upload or CDN path in this slice. If R2 chooses a merchant-of-record or
external object store later, that is a new adapter decision: preserve the same release hash,
company guard, capability expiry, entitlement idempotence, and refund revocation contract.

## M1 — Install the bundle (M; founder)

Resolve both bundle groups together through the normal reviewed release process. The resolver
installs `factory_digital` and its `templates` dependency, plus `digital_sale` and its digital
kernel dependency:

```bash
ODOO_INSTALL_BUNDLE=private-digital:commerce,private-factory:digital
```

Use the estate's reviewed release command for the selected instance; do not run an ad-hoc module
install against production. A dry run must show `digital_sale`, `factory_template`, and
`factory_digital` before the founder approves the change. Installation is inert: no product,
entitlement, link, or delivery row is created.

**R — verify after install:**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call ir.module.module search_read \
  '[["name","in",["digital_sale","factory_template","factory_digital"]]]' \
  -k '{"fields":["name","state"],"order":"name"}'
```

For `kod-desk`, use its explicit Odoo profile instead. Expect all three modules to be `installed`.

**Rollback:** while no R6 product is in use, remove `factory_digital` through the reviewed addon
rollback path. Do not remove `digital_sale` or `factory_template` if another slice uses them.

## M2 — Prepare the factory artefact (M; founder + independent reviewer)

1. Register and verify the required font/image assets with licence evidence. The uploader cannot
   verify their own asset.
2. Import the brand kit and template from the committed Dokploy sources. Confirm the kit names an
   independent expert reviewer.
3. Create a factory work order and materialise the printable. Run the expert then founder tiers;
   the producer cannot approve their own tier.
4. Release the render through the factory release predicate. A direct `state = released` write,
   an unbound order, or a changed artefact is refused.

**R — verify the exact released record:**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template.render search_read \
  '[["state","=","released"]]' \
  -k '{"fields":["id","display_name","company_id","artefact_id","artefact_sha256"],"limit":10}'
```

Record the render ID, company, attachment ID, and hash in the change ticket. Do not paste buyer
names, email addresses, or capability tokens into the ticket.

## M3 — Attach the released render to the sellable product (M; founder)

Create or select the digital product's ordinary sellable twin. Bind the released render from the
same company, then use the factory adapter's sale action. The adapter refuses unreleased, missing,
malformed, or foreign references with the same non-disclosing lookup refusal; it also guards both
create and write.

```text
Product:       <digital product ID / code>
Render:        <released render ID from M2>
Company:       <the R2 seller-of-record company>
```

**R — verify:** the product's `factory_render_id` is the released render, both company IDs match,
and the sellable twin is the exact product used by the quotation. A product must not be marketed
until these three values agree.

## M4 — Paid checkout and one delivery (M; founder, synthetic first)

Use a test partner and the selected seller-of-record company first. Confirm a sale order, post the
invoice, and move it to paid through the estate's normal payment path. Do not call the entitlement
grant method as a shortcut. The sale and invoice guards run before money is committed; a foreign
company twin must refuse there, not make the invoice unreadable in payment-state computation.

**R — verify the acceptance invariants:**

- one paid line creates one entitlement; replaying the paid callback changes no count or provenance;
- the entitlement records its first sale-line/product provenance once;
- the generated URL contains only an opaque random token;
- `factory.digital.link` stores the token hash, expiry, budget, and revocation state, never the raw
  token, filestore path, buyer email, or client-supplied expiry;
- the first request serves the released bytes and creates one delivery ledger row;
- a repeated request follows the configured link budget and never bypasses entitlement state;
- a raw-SQL attachment tamper is refused at delivery, alerts the founder, serves no bytes, and
  creates no delivery row.

The delivery path re-reads the attachment and compares its SHA-256 to the released render's
recorded hash immediately before serving. The release-time ORM immutability guard is not enough
against a database-level tamper; this delivery check is mandatory.

## M5 — Refund and revoke drill (M; founder)

Run this with the synthetic paid order before accepting a real order:

1. Save the entitlement's link URL and delivery count.
2. Post a **full** refund/cancellation through the normal payment/refund path.
3. **R —** verify the entitlement is revoked, the link returns the named refusal, and the old URL
   does not expose whether another entitlement exists.
4. Create a second paid purchase for the same buyer/product. **R —** verify the buyer's one
   entitlement remains active only when another paid purchase is live.
5. Run a **partial** refund. **R —** verify access remains and the entitlement chatter contains a
   manual-review note; partial refunds do not silently revoke the entire product.

Record order/invoice/entitlement/link IDs only. Never record the raw URL token. If a refund event
is replayed, the result must remain revoked and no duplicate delivery or chatter storm may occur.

## M6 — Rollback and incident handling

- **Unreleased or missing render:** unbind the product; no entitlement or link is valid.
- **Hash mismatch:** stop sales of the product, leave the released row untouched, preserve the
  founder alert and delivery ledger, and investigate the attachment/filestore backup. Do not
  overwrite the recorded hash to make the delivery pass.
- **Wrong company:** stop checkout and correct the seller-of-record/product binding. Do not bypass
  the company guard with `sudo` or a direct database update.
- **Refund dispute:** preserve the invoice, credit note, entitlement, link, and delivery rows;
  support resolves the partial/full classification manually.
- **Full feature rollback:** stop new checkout, revoke affected entitlements through the approved
  incident procedure, then uninstall only `factory_digital` after confirming no other slice depends
  on its tables. The release attachment remains governed by `factory_template` until separately
  handled.

## Evidence to record

The change ticket/result row must contain: target instance, R2 seller-of-record decision, released
render ID and hash, product/twin ID, synthetic order/invoice IDs, entitlement and delivery counts,
full-refund result, partial-refund note, the exact refusal code for a cross-company attempt, and
the operator/reviewer identities. It must not contain raw capability tokens, buyer contact data,
filestore paths, or secrets.
