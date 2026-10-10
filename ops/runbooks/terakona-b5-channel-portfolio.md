# Runbook: Terakona niche channel portfolio (B5), founder-gated

This runbook takes the Terakona channel portfolio from **built-local** to **operating**.
Built-local means committed channel data, contract-tested, with an offline evaluator. Operating
means real niches, approved sub-brand kits, platform accounts, live performance evidence and
founder-owned stop decisions.

Every step here is a founder step (**M**), except the checks marked read-only. Nothing in this
runbook has been applied yet.

- The estate is kodeme.io only (`-p kodemeio`). No idtpp host, key, job or agent is touched.
- No channel account is created and nothing is posted by any step in this repo.
- The evaluator only ever **proposes** cancellations.

Slice of record:

- Track B design, 2026-09-28 (B5, §3.1–3.3, TB-D6,
  TB-D9);
- Track B plan, 2026-09-28 (slice TB5).

Roadmap row: **B5**.

## What is committed (read-only)

| Piece | Path | Notes |
|---|---|---|
| Contract | `contracts/channels/channel.v1.schema.json` (+ `contracts/examples/channels/`) | `code`, `kit`, F8 `platform`, `cadence_per_week`, `formats` (video allowed, disabled), `stop_rule` in G2 threshold format, with `on_miss.day_60` `pivot`/`stop` and `on_miss.day_90` = `stop` |
| Channel A | `channels/terakona/niche-a.yaml` | Instagram, carousel and caption, 3/week, views ≥ 5000 (PLACEHOLDER) |
| Channel B | `channels/terakona/niche-b.yaml` | TikTok, script and caption, 5/week, views ≥ 10000 and watch time ≥ 36000 s (PLACEHOLDER) |
| Sub-brand kits | `brands/terakona-niche-a.yaml`, `brands/terakona-niche-b.yaml` | `draft` PLACEHOLDER, `expert_logins: []`, a `kit uji coba` disclaimer, distinct palette and font per kit |
| Line | `product_lines/terakona-channels.yaml` | One `content_piece` per channel under its own kit, one variant per enabled format, `publish: none`, `launch: []` |
| Evaluator | `ops/channel_portfolio/evaluate.py` (+ README, tests, synthetic series and expected decision) | Stop rule → G2 → `continue`/`pivot`/`stop`, plus a list of pending F10 items proposed for cancellation |

🔴 **Everything here is EXAMPLE CONTENT (TB-D9), and both niches are placeholders.** Each piece
renders under its channel's draft kit, and that kit requires a `kit uji coba` disclaimer.
Both F8's `content_brand_rules` and the kernel's `brand_rules` refuse any piece that lacks it, so
a placeholder channel's content cannot pass for a real channel's.

Read-only proof, available at any time:

```bash
uv run pytest -q deploys/tests/test_contracts_channels.py ops/channel_portfolio/tests \
  deploys/tests/test_placeholder_kits.py deploys/tests/test_contracts_product_lines.py
uv run python ops/channel_portfolio/evaluate.py --channels-dir channels \
  ops/channel_portfolio/examples/performance-90d.synthetic.json
```

## Local end-to-end (built-local bar, with the K2 harness)

This runs on a disposable `odoo_test_tb_*` database once K2's product-line harness lands in
kodemeio-odoo `bin/product_line/`. TB5 ships only that harness's `performance` step,
`bin/product_line/steps/performance.py`. The harness must:

1. **Content.** For each `content_piece` item:
   - resolve `ref` to `channels/<brand>/<niche>.yaml` (`<brand>-<niche>` == `ref`);
   - import the item's kit, with the synthetic expert/founder overlay in the test DB only,
     recorded as `experts: synthetic-overlay`;
   - make one F8 piece per variant, where the variant key is the F8 kind, on the channel's
     platform;
   - run checks, then expert approval, founder approval and release.

   Acceptance: the two channels' copy and snapshot hashes differ (brand switch).
2. **L3.** Inject the kit's first forbidden phrase into a copy of one piece. The check must refuse
   by name, never echoing the copy:
   - niche-a: `pasti viral`;
   - niche-b: `pasti viral`.
3. **L4.** Release needs an expert and then the founder (two distinct synthetic users). A direct
   `state` write refuses, and so does an agent write.
4. **L9.** Before approval, no F10 publication exists. After release, the harness makes one manual
   `creator_handoff` publication per piece through `action_schedule_publication`.
5. **Performance.**
   - As a `factory_performance.group_factory_performance_manager` user, import synthetic
     publication metric snapshots into F0f.
   - Run the `performance` step for `{as_of_date, channels:[{code, kit}]}`. It returns the
     evaluator input and is read-only.
   - Pass that input to `evaluate.py`.

   The evaluator output must be identical across two runs.
6. **L10.** No PII appears in the run log or result JSON. Only ids, codes, shas and states are
   written.

The synthetic 90-day series in `ops/channel_portfolio/examples/` is the reference outcome:
niche-a `continue` (`day_90_met`) and niche-b `stop` (`day_90_missed`). Publications 203 and 204
are proposed for cancellation.

## Operational gates (founder, M)

| # | Gate | What closes it |
|---|---|---|
| G1 | **Real niche choice** | Pick the niches. Rename or replace `channels/terakona/niche-*.yaml` and their kits (`code` = `<dir>-<stem>`, kit `code` = file stem). |
| G2 | **Approved sub-brand kits** | Write the real voice, audience, forbidden phrases, disclaimers and palette. Keep `status: draft` and the `kit uji coba` disclaimer until approval. Then set `active`, drop `PLACEHOLDER` and the trial disclaimer, and update `test_terakidz_stays_the_one_active_kit` in the same reviewed change. |
| G3 | **Named experts** | Put real reviewer logins in `reviewers.expert_logins`. The synthetic overlay never leaves the test DB. |
| G4 | **Platform accounts** | Create one publisher account per channel in Odoo F10 (`content.publisher.account`) on the kod estate. The mode stays `creator_handoff` until the founder approves anything else. |
| G5 | **Real thresholds** | Set `stop_rule.metrics` thresholds and the day-60 action per channel. Thresholds are G2 decimal strings. Supported metrics are `views` and `watch_time_s` (publication snapshots) and `sales` (paid lines); clicks and completions stay unavailable in F0f. |
| G6 | **Live metrics ingestion** | A privacy-reviewed provider adapter fills `content.publication.metric.snapshot`, and F0f imports it. This slice builds no live ingestion. The `performance` step refuses any database not named `odoo_test_tb_*`. Running it on production is a separate, founder-approved change. |
| G7 | **Hook data (F7)** | Hook and retention inputs for the pivot playbook come from F7 once it exists. They are not part of the stop rule. |
| G8 | **Video (TB-D6)** | F6 (VID1) is now built-local in kodemeio-hatchet, but the plan keeps TB-D6: `video` stays `enabled: false`. Lifting it needs a founder decision, a contract change (the `video` → `enabled: false` rule in `channel.v1`), F6 → F8/F10 wiring (F8 has no video kind) and a platform format entry. |
| G9 | **Acting on a stop** | Read the evaluator's `cancellation_proposals`. The founder cancels each pending F10 item by hand in Odoo (`action_cancel`). Nothing is cancelled automatically. Record the decision outside the tool, as the G2 README requires. |

## Rollback

Everything here is data. Reverting the commit removes the channels, kits and line. There are no
F10 publications or F0f rows outside disposable test databases.
