# Channel portfolio evaluator (Track B B5)

`evaluate.py` decides `continue`, `pivot` or `stop` for each niche channel in a portfolio. It
works offline and reads only. It never cancels, publishes, writes or calls a service. For a
stopped channel it lists the channel's pending F10 publications as a **proposal** for the founder
to cancel by hand. Its output is `proposal_only: true`.

```bash
uv run python ops/channel_portfolio/evaluate.py --channels-dir channels \
  ops/channel_portfolio/examples/performance-90d.synthetic.json
```

## Inputs

- **Channels.** These are the committed `channels/<brand>/<niche>.yaml` files, in
  `contracts/channels/channel.v1.schema.json` format. Pass them with `--channels-dir` or with
  one or more `--channel <file>`. Each channel is validated against the contract and then
  resolved:
  - its kit must exist in `--brands-dir` (default `brands/`) and must not be `retired`;
  - its `code` must be `<dir>-<stem>`;
  - each enabled format must be one its F8 platform accepts;
  - no two channels may share a kit.

  Any of these problems refuses the whole portfolio, and the message names the problem.
- **Evaluator input (JSON).** This is one explicit file. The product-line harness `performance`
  step (kodemeio-odoo `bin/product_line/steps/performance.py`) writes it from
  `factory.performance.snapshot`; the example here is synthetic. It holds:
  - `schema_version`, `as_of_date` and `currency`;
  - `channels[]`: each channel's `code`, its `launch_date` (or `null` when not launched) and
    its `pending_publications[]` (`publication_id`, non-terminal F10 `state`);
  - `observations[]`: F0f `research_export` rows plus `observation_id` and `publication_id`.
    Those two ids are what lets the evaluator resolve corrections and point snapshots.

  Objects are closed and references are pattern-checked. Unknown keys, free text, URLs,
  negative or non-finite values, and brands outside the portfolio are refused by name.

## How a decision is made

1. **Aggregate per channel kit, as of each checkpoint's due date** (launch + 60 and + 90 days).
   - `views` and `watch_time_s` are running totals, so each publication contributes its latest
     snapshot, and those are summed.
   - `sales` rows are daily counts and are added up.
   - A correction (`supersedes_id`) replaces the row it supersedes. If there are several
     corrections, the highest id wins.
2. **Feed G2.** The channel's `stop_rule.metrics` and those aggregates go to the G2 scorecard
   evaluator, `evaluate_scorecards` in `ops/scripts/teracorp_ops_metrics.py`. G2 is imported
   through the single `G2_SCRIPT` reference, not copied. G2 reports `met`, `missed` or
   `unmeasured` per metric and checkpoint.
3. **Decide.** Apply the first rule that matches:

   | G2 result | Decision | Reason |
   |---|---|---|
   | Day 90 is due and any metric missed | `on_miss.day_90` (always `stop`) | `day_90_missed` |
   | Day 90 is due and every metric met | `continue` | `day_90_met` |
   | Day 60 is due and any metric missed | `on_miss.day_60` (`pivot` or `stop`) | `day_60_missed` |
   | Day 60 is due and every metric met | `continue` | `day_60_met` |
   | Nothing is measured | `continue` | `unmeasured` |
   | No launch date | `continue` | `not_launched` (G2 is not called) |

The output is serialised with sorted keys and compact separators, so it is byte-stable.
`examples/decision-90d.expected.json` is the committed decision for the synthetic 90-day series:
`terakon-niche-a` continues, `terakon-niche-b` stops, and niche-b's two pending items are
proposed for cancellation.

## Limits

- Synthetic fixtures are not targets or evidence. The thresholds in `channels/` are
  placeholders, and the founder sets the real ones.
- Live metrics ingestion, platform accounts and real niche kits are operational gates. See
  `ops/runbooks/teracorp-b5-channel-portfolio.md`.
- Video is listed in every channel but stays disabled (TB-D6).

Tests:

```bash
uv run pytest -q ops/channel_portfolio/tests deploys/tests/test_contracts_channels.py
```
