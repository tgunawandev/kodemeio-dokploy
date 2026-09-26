# Teracorp CW1 — privacy draft for counsel (M5)

**Status: DRAFT — not legal advice, not approved copy.** This is the
engineering team's best-effort description of what the system actually
does, written so counsel can turn it into compliant notice text and a real
retention policy. Nothing in this file should be shown to a customer as-is.

- Design: `2026-09-26-teracorp-cw1-chatwoot-design.md` D6-D9, §8 (M5, open
  questions 4-5)
- Legal basis: UU PDP (Indonesia's Personal Data Protection Law, 27/2022) —
  referenced in the design as LEG1, not otherwise researched here.

## 1. Data map

| Data | Where it lives | Who/what touches it | Retention (proposed, §3) |
|---|---|---|---|
| WhatsApp phone number (E.164) | Chatwoot contact record; `res.partner.phone` (Odoo); Chatwoot contact custom attribute `odoo_partner_ref` (the link) | kido_chat (identity.py resolves it, never logs it); Chatwoot; Odoo | Tied to the contact's retention (below) |
| Display name (as given in WhatsApp profile, editable by staff) | Chatwoot contact record; `res.partner.name` | Same as phone | Same |
| Message text | Chatwoot transcript only. **Never** written to kido_chat's SQLite store, Odoo, Hatchet task inputs, logs, or Mattermost. The model (LiteLLM `kido` key) sees the text of the CURRENT turn only, plus a pseudonymous `conversation_ref` — no phone, no name | Chatwoot (transcript); the vetted LLM provider (grp-personal), per-request, no training/bounded retention per the founder's M4 approval | Chatwoot's own transcript retention (open question, below) |
| Order lines / product refs | Odoo `sale.order` (draft, then confirmed by staff) | kido_chat → order_intake → Odoo (MCP, ids only signed over HTTP) | Odoo's normal retention (financial records, not covered by this runbook) |
| Payment code (QR string / VA number) | Odoo (via PAY1/Midtrans), sent to the customer through Chatwoot | Never logged, never sent to the model, never sent to Mattermost (spec D10) | Not stored by kido_chat at all — read from Odoo at send time only |
| Consent state (`SETUJU` / `STOP`) | Chatwoot contact custom attributes `privacy_consent_version`, `privacy_consent_at`, `kido_opted_out_at` | kido_chat (identity.py) | Same as the contact |
| Conversation metadata (ids, timestamps, tool calls, error codes) | kido_chat SQLite store | kido_chat only | 30 days, then pruned (code-enforced, spec §5 "Retention") |

**Processor list** (who receives personal data, for a UU PDP processor
registry):

1. **Chatwoot** (self-hosted, this stack) — controller's own infrastructure,
   not a third-party processor in the traditional sense, but still the
   system of record for transcripts.
2. **Meta** (WhatsApp Cloud API) — carries every message; Meta's own privacy
   terms for WhatsApp Business Platform apply independently of this system.
3. **The vetted LLM provider** (grp-personal, founder-approved per M4) —
   receives message text + catalogue context per turn. Founder's approval
   requires no-training and bounded-retention terms (spec D8) — get the
   provider's DPA/data processing terms before M4 is signed off, not after.
4. **Odoo (this org's own instance)** — order and partner records.
5. Midtrans (PAY1, payment processor) — out of this runbook's scope; see
   PAY1's own design doc for its processor terms.

## 2. Notice text (draft — counsel to finalize)

First KIDO reply in a new conversation, before anything else, in both
languages available (send the customer's apparent language; default `id`):

**Indonesian (id):**
> Halo! Anda sedang berbicara dengan asisten otomatis Terakidz (KIDO). Pesan
> Anda dapat diproses oleh sistem otomatis untuk membantu menjawab
> pertanyaan dan memproses pesanan. Kebijakan privasi kami:
> [LINK — counsel to provide]. Balas **SETUJU** untuk melanjutkan, atau
> **STOP** kapan saja untuk berhenti dan berbicara langsung dengan staf kami.

**English (en):**
> Hi! You're talking with Terakidz's automated assistant (KIDO). Your
> message may be processed by an automated system to help answer questions
> and process orders. Our privacy policy: [LINK — counsel to provide].
> Reply **SETUJU** to continue, or **STOP** at any time to opt out and talk
> to a human directly.

**Consent keyword:** `SETUJU` (Indonesian for "agreed"). Case-sensitive
match on the whole message (not a substring — "setuju dong" does not
trigger it, per `identity.py`'s `handle_keywords`, so a customer casually
using the word in a sentence isn't silently opted in).

**Opt-out keywords:** `STOP` or `BERHENTI` (Indonesian for "stop"), same
whole-message matching rule. Either immediately: adds a `kido-off` Chatwoot
label, records `kido_opted_out_at` on the contact, and hands the
conversation to staff (`customer_asked` reason code) — KIDO stays silent for
that contact from then on (Task 6's eligibility check treats
`identity.opted_out` the same as the `kido-off` label, per ruling R8).

**Consent scope**: catalogue questions (product/price lookups) do NOT
require consent — only `request_order` (creating an order, which requires
storing a partner record) is refused before `SETUJU` is recorded. See open
question 5 below — counsel may decide the notice alone is enough even for
catalogue-only chats, without a keyword gate.

## 3. Retention proposal (needs counsel sign-off — nothing auto-deletes yet)

| Data | Proposed retention | Why |
|---|---|---|
| Chatwoot transcripts | 12 months (example — **counsel decides the real number**, open question 4) | Balances customer-service usefulness (repeat questions, dispute resolution) against data-minimization |
| kido_chat SQLite store (ids, hashes, consent flags) | 30 days (already code-enforced, `store.prune`) | Operational only — nothing here is needed past the point a conversation is either answered or handed off and resolved |
| Odoo partner/order records | Per Odoo's existing retention (financial/business records — out of this runbook's scope) | Governed by accounting/tax retention rules, not this design |

**Nothing auto-deletes today.** Until counsel sets a real transcript
retention period, Chatwoot's transcripts are kept indefinitely by default —
this is a known gap (spec §8 open question 4), not a decision.

## 4. Deletion procedure (a customer's right-to-delete / right-to-be-forgotten request)

1. **Chatwoot**: delete the contact (Settings → Contacts → find by phone →
   delete). This removes the transcript and the custom attributes
   (`odoo_partner_ref`, consent state, opt-out timestamp).
2. **Odoo**: archive (do not hard-delete) the linked `res.partner` record —
   archiving preserves referential integrity with any existing sale orders
   (financial records cannot be deleted per normal accounting retention
   rules) while removing the partner from active use and search.
3. **kido_chat SQLite store**: the contact→partner cache entry and any
   `seen_messages`/`replies` rows referencing that conversation are already
   subject to the 30-day prune; a manual purge is not built (out of scope
   for CW1) — if a deletion request needs the store cleared sooner than 30
   days, this is a manual SQL step against the mounted `kido-chat-state`
   volume, not a self-service tool today. Flag this as a roadmap item if
   deletion SLAs require faster-than-30-day clearing.
4. **The LLM provider**: per the founder-approved provider's own
   no-training/bounded-retention terms (M4) — nothing in kido_chat's own
   code can reach back and delete a prompt already sent; this is why M4's
   approval criteria require bounded retention in the first place.

## 5. Child data (spec D9 — code guarantees, not a policy)

KIDO never asks for a child's name, age, school or photo. The code
guarantees: no free text is ever written to Odoo, envelopes, Mattermost,
logs or kido_chat's store; inbound media (images, voice) is never sent to
the model — KIDO replies with a fixed text and hands off instead. The
residual risk — a parent volunteers a child's details in free text, which
then reaches the vetted LLM provider as part of that turn's prompt — is not
something code can prevent; it is recorded here for counsel's LEG1 review,
not solved by this design.

## Open questions for counsel (blocking M5 sign-off)

1. Final notice wording (id/en) — the draft above is engineering's
   best-effort placeholder.
2. Chatwoot transcript retention period (proposed: 12 months — needs a
   real decision).
3. Does Terakidz need the `SETUJU` consent gate for catalogue-only chats,
   or is the notice alone sufficient, reserving explicit consent for
   ordering only (current code behavior)?
4. Is 30-day kido_chat store retention acceptable, or does UU PDP require a
   shorter window for message metadata?
5. Deletion SLA — does a customer's delete request need same-day handling
   in kido_chat's SQLite store, ahead of the 30-day prune?

## What this runbook does NOT cover

- Meta/WhatsApp terms (Meta is a separate processor with its own privacy
  terms) — `teracorp-cw1-meta-whatsapp.md`.
- The actual `KIDO_ENABLED`/kill-switch mechanics — `teracorp-cw1-rollout.md`.

## Rollback

Not applicable — this is a policy document, not a deployed system. If
counsel requires changes to the notice/consent flow before go-live, the
code changes (SOUL text, `identity.py` keyword matching) go through the
normal kodemeio-hatchet review process, not through this runbook.
