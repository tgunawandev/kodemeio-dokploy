# Runbook — Social content factory on kodeme.io (founder-gated)

Takes the social content factory (F8 / SOC1) from *built and tested locally* to **brand-switched,
approval-gated content output** on the kodeme.io estate: a `social` work order materialises a
content piece whose voice, rules and palette come from the brand kit, the copy is judged by three
automatic checks, approved by an expert and then by the founder, and **released — not published**.

Slice of record: `kodemeio-docs/superpowers/specs/2026-09-27-teracorp-social-content-factory-design.md`
and `…/plans/2026-09-27-teracorp-social-content-factory.md` (Tasks 1–4). Roadmap row: **F8** (SOC1).

Everything in this runbook is a founder step (**M**) except the read-only checks, which are marked
as such. **Nothing here has been applied.** Every command uses the kodeme.io estate only
(`-p kodemeio`, or `./odoo.sh kod|kod-desk … prod`); no idtpp host, bucket, key or job is touched,
and no production write happens anywhere in this document without `--yes`.

## Facts this runbook is built on (verified read-only, 2026-09-27)

| # | Fact | How it was read |
|---|---|---|
| 1 | `factory_content` is committed on `18.0` (`kodemeio-odoo` **491804e55** T1, **d2393db21** T2, **b9e9ebb33** T3) — **112 tests, 0 failed**; `factory_base` 140 and `factory_landing`/`factory_template` stay green on their own slim DB | `TEST_DB=odoo_test_factory_f8 ./odoo.sh dev t factory_content` |
| 2 | Bundle group `content` exists in `install/private-factory.yaml` (depends `core`, one module) and the bundle `requires` `private-content`; `bin/validate-bundles` reports **0 errors** | `bin/validate-bundles` |
| 3 | The channel limits are committed: `factory_content/data/content_channel_rules.yaml` — four channels (`tiktok`, `youtube`, `instagram`, `facebook`), materialised into `factory.content.channel.rule` rows on install **and on every upgrade** | the file; the module's `CLAUDE.md` |
| 4 | 🔴 **Only the Terakidz kit is real.** `brands/terakidz.yaml` carries a written voice, a do/don't list, forbidden phrases, a required disclaimer and an AI-disclosure line; `terakon` is a draft fixture, and **`terafin` and `terakod` have no kit file at all** | `kodemeio-dokploy/brands/` |
| 5 | 🔴 **No `content.transport` for an LLM gateway exists on this estate.** The content kernel's transports are `veo` and `seedance` (video), and every piece is enqueued through that ledger; the LiteLLM-keyed *copy-writing* transport the spec's D4 anticipates is **not wired anywhere**, so the account a piece is generated under must be one the operator has created and priced | `kodemeio-odoo/src/private/content/`; spec D4 |
| 6 | 🔴 **Nothing publishes.** `action_release_content` sets `released`; publication (TikTok/YouTube/Meta) is F10 (`publishing`) and `content_publish_base` is not even a dependency of this module | spec D5; `tests/test_acceptance.py::test_nothing_is_published` |
| 7 | The content kernel's own FAKE transport is registered only under `--test-enable`; no production instance has a second generation path | `content_base/models/__init__.py` |

🔴 **The two things that can refuse this whole slice on a real instance**:
(1) **no content account with a price line** — the framework returns a *cancelled* generation row
for a missing price and the adapter refuses materialise by name, so nothing is produced until a
manager creates one; (2) **a brand kit that is not usable** — a kit whose fonts are not verified
`font` assets refuses at derive time. Step M3 therefore comes BEFORE M4.

---

## M0 — Decide: which instance, which channels, which brands (M)

| Question | Options | Cost |
|---|---|---|
| Which Odoo instance | `./odoo.sh kod prod` (erp.kodeme.io) is the default here; `kod-desk` for the desk instance | one `-p` profile per instance |
| Which channels are in scope FIRST | the committed four are `tiktok`, `youtube`, `instagram`, `facebook` — all four have a module on the estate (`content_tiktok`, `content_youtube`, `content_meta`) | the spec's founder gate 2; a channel not in the file is refused by name |
| Which brands | **Terakidz only, today.** Terakon/Terafin/Terakod are placeholders (fact 4) | see M2 |
| Which copy-writing engine | none is wired (fact 5) | a follow-up: either a `content.transport` for the gateway, or the composition runs outside and the piece records it |

**Founder decisions to record here (fill in and commit):**

```
Target instance:        [ ] kod (erp.kodeme.io)     [ ] kod-desk (desk.kodeme.io)
Channels in scope first:[ ] tiktok  [ ] youtube  [ ] instagram  [ ] facebook
Brands turned on:       [ ] terakidz                [ ] terakon  [ ] terafin  [ ] terakod
Copy engine:            [ ] accept "no LLM transport yet" (piece materialise still requires an account)
                        [ ] commission the gateway transport first
```

**Rollback:** none — this step decides, it does not change anything.

---

## M1 — Install the bundle group (M)

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
git push origin 18.0                               # `release` builds from origin/18.0, not the tree

./odoo.sh release kod prod install factory_base,factory_content --yes
# or, if the app image already carries them:
./odoo.sh addon kod prod install factory_base,factory_content --yes
```

🔴 `factory_content` depends on `content_base`, which the bundle `requires` as
`private-content`; if the content bundle is not installed on that instance, install its `core`
group in the same act. The group needs the content framework's `core` group — the generation
ledger this adapter enqueues through.

Installing creates **no** work order, no kit, no asset, no account, no price line and no piece.
It DOES create the four channel-rule rows from the committed file (and refreshes them on every
upgrade).

**Read-only check after install:**

```bash
./odoo.sh kod prod sql run "SELECT channel, caption_max, hashtag_max, thread_max, carousel_max, kinds, aspect_ratios FROM factory_content_channel_rule WHERE company_id IS NULL ORDER BY channel"
```

**Rollback:** `./odoo.sh addon kod prod uninstall factory_content --yes`. The channel rows go with
it; no order, kit or asset is touched.

---

## M2 — Confirm the channel limits (M) — 🔴 the founder gate the spec names

Every number in `factory_content/data/content_channel_rules.yaml` is a **PROPOSAL**, transcribed
at build time and not confirmed against any platform's live documentation. Confirm each one, then
commit the change — the file is the source of truth and the rows follow on the next upgrade.

| channel | caption_max | hashtag_max | thread_max | carousel_max | ai_disclosure |
|---|---|---|---|---|---|
| tiktok | 2200 | 30 | 0 | 35 | required |
| youtube | 5000 | 15 | 0 | 0 | required |
| instagram | 2200 | 30 | 0 | 20 | required |
| facebook | 63206 | 30 | 0 | 10 | required |

- `thread_max: 0` means **the channel has no thread form** and a `thread` piece refuses by name.
  That is the truthful value for all four today, and it is a founder item: the blueprint's
  "threads" output form has no channel to land on yet.
- `carousel_max: 0` (youtube) means the same for a carousel.
- The `required_disclosure` line on every channel is the copy-level minimum this factory
  enforces; the platform's own AI label (`is_aigc`, `containsSyntheticMedia`) is a *field*, and
  it is F10's to set.
- A limit changes by editing the YAML and committing the reason; the rows are read-only in the
  UI on purpose. A company that needs a different limit gets its own row through the ORM, which
  the resolver prefers.

**Rollback:** edit the file back and upgrade; the rows follow.

---

## M3 — The assets and the kit, BEFORE any content (M)

Order matters: a piece whose kit font is not a verified asset cannot be derived at all.

1. **Register and verify the fonts the Terakidz kit names** (`asset:font-nunito`), with licence
   evidence on file, verified by a **second** person (the uploader never verifies their own
   asset). Same for any image a piece will use — a raw URL is refused; the piece names
   `asset:<key>`.
2. **Import the kit** (`brands/terakidz.yaml`) as a factory manager or the founder:

   ```bash
   ./odoo.sh kod prod shell call factory.brand.kit import_kit '["<payload json>", "<sha256>"]'
   ```

   The kit's `reviewers.expert_logins` must name at least one person who is not the submitter:
   **submitting refuses, by name, with no independent expert.**

3. 🔴 **Terakon, Terafin and Terakod are PLACEHOLDERS.** Terakon is a draft fixture and Terafin
   and Terakod have no kit file at all. A `social` order carrying a placeholder kit will
   materialise and then fail its checks the moment the kit's rules bite — which is correct, but
   it is not a way to ship content. **Turn on a brand only when its kit carries a real voice, a
   real do/don't list, its forbidden phrases, its required disclaimer and its AI-disclosure
   line.** Record here which brands are real:

   ```
   terakidz:  REAL   (voice, rules, disclaimer, AI disclosure)
   terakon:   PLACEHOLDER — draft fixture, no confirmed voice
   terafin:   PLACEHOLDER — no kit file
   terakod:   PLACEHOLDER — no kit file
   ```

**Rollback:** archive the kit (`active = False`); nothing that was already released changes, and
a new piece cannot be derived against it.

---

## M4 — The content account and its price line (M)

The framework enqueues a `content.generation` row and refuses (a **visible, cancelled** row) when
no price line exists — the adapter then refuses materialise by name. So:

```bash
./odoo.sh kod prod shell call content.account create '[{"name": "...", "transport_code": "..."}]'
# then, as a content manager, a price line per (transport, medium, model scope)
```

🔴 **Fact 5**: this estate has **no LLM transport for copy**. The transports that exist generate
video. Until a gateway transport is wired, the account a piece is generated under is whatever
exists, and the composed prompt is what that transport receives. Decide explicitly which of these
this instance does:

- **(a)** accept it — the piece is materialised, its prompt is composed from the kit, and the
  ledger row records what was asked for; the copy itself is written outside and recorded;
- **(b)** commission the `content.transport` for the gateway first.

**Rollback:** deactivate the account; nothing spends while it is inactive.

---

## M5 — Run one piece end to end (M, on ONE order)

```bash
# 1. a `social` work order, with the kit and a brief
./odoo.sh kod prod shell call factory.work.order create '[{"line_id": <social line id>, "brand_kit_id": <kit id>, "goal": "...", "inputs": {...}}]'
```

`inputs` (full reference in the module README):

```json
{
  "project": <content.project id or name>,
  "channel": "tiktok",
  "kind": "caption",
  "copy": "…the copy…",
  "hashtags": ["#terakidz"],
  "assets": ["asset:image-terakidz-hero"],
  "options": {"locale": "id_ID", "aspect_ratio": "9:16"}
}
```

Then, in order — **each step refuses rather than warns**:

1. `action_start()` → `in_progress`;
2. `action_materialize_content()` → a content brief, the kit-composed prompt, **one** generation on
   the framework's ledger, and a `derived` piece whose `voice_snapshot`, `rules_snapshot`,
   `theme_snapshot` and `input_sha256` are recorded;
3. `action_submit_content(piece)` → the three checks, then the expert tier. A refusal returns the
   order to `in_progress`, unbound, and **names the failing check and the rule**;
4. the expert approves, then the founder approves;
5. `action_release_content(piece)` → `released`.

🔴 **The release re-verifies**: a licence withdrawn, or a kit archived, between the approval and
the release refuses **by name**. That is not a bug to work around; re-approve a piece that was
derived against the current state.

🔴 **What "manual publishing" means until F10.** `released` means *approved, hash-bound and
licence-clean*. It does **not** mean posted. Until F10:
a person copies the released piece's copy out of Odoo and posts it through the platform's own
app or console, as themselves. Nothing in this slice calls TikTok, YouTube, Instagram or
Facebook, holds an OAuth grant, or schedules anything. The AI-disclosure and per-channel
disclosure texts the factory enforced are in the copy, so the platform's own label — where it
has one — is the second, platform-side half of the same obligation, and it is set by hand.

**Rollback:** cancel the order (its piece is cancelled with it). A released piece is evidence and
is never cancelled or deleted — a new work order is the way to change it.

---

## M6 — Turn it on for a brand (M)

1. Confirm M2 (limits) and M3 (kit and assets) for that brand.
2. Raise the `social` line's `wip_limit` if more than one piece should be in flight
   (it ships at 1).
3. Give the people who will run it the `Content Factory User` group (it implies the factory's and
   the content kernel's user groups), and make sure the kit names the experts.
4. Record here:

```
Brand turned on:        ______________
Date:                   ______________
Channels:               ______________
Kit version (sha256):   ______________
WIP limit:              ______________
Experts named:          ______________
```

**Rollback:** archive the kit, or set the line's `wip_limit` to 0 (it stops the line).

---

## Read-only checks an operator can run any time

```bash
# the channel rules the checks actually read
./odoo.sh kod prod sql run "SELECT channel, caption_max, hashtag_max, thread_max, kinds FROM factory_content_channel_rule ORDER BY channel"

# pieces by state, and the ones waiting on a person
./odoo.sh kod prod sql run "SELECT state, count(*) FROM factory_content_piece GROUP BY state ORDER BY state"

# generations the factory enqueued, with their cost
./odoo.sh kod prod sql run "SELECT state, count(*), sum(cost_estimated) FROM content_generation WHERE operation_key LIKE 'factory-content-%' GROUP BY state"
```

## Out of scope for this slice (deliberately)

Publishing and scheduling (F10), video (F6), hooks with performance data (F7), the paid-ads
family (`content_ads`), and any platform transport beyond the committed rule data. No brand kit
ships with the module, no deployment happens as part of it, and the runbook above is what turns
it on.
