# Teracorp G2/G3 metrics — local operator tool

`../scripts/teracorp_ops_metrics.py` validates an explicitly supplied product scorecard or
summarizes aggregate founder-hours. It is offline and read-only; it does not fetch live metrics,
connect to Odoo, update a scorecard, or make a continue/pivot/kill decision.

The files under `examples/` are synthetic fixtures only. They are not Teracorp product targets,
actual founder time, or acceptance evidence. Copy an example to a separately controlled local
working file, replace all synthetic values, and have the founder set thresholds before using it.
Do not put customer/person names, credentials, incident narratives, or other sensitive details in
these inputs.

## G2 — product scorecards

```bash
python3 ops/scripts/teracorp_ops_metrics.py validate-scorecards \
  ops/metrics/examples/product-scorecards.synthetic.json
```

Each scorecard supplies a canonical `as_of_date`; each product supplies its own currency and one or
more founder-defined metrics with explicit unit identifiers. The only allowed comparisons are
`gte` and `lte`; targets are explicit decimal strings so units and rounding are not silently
guessed. Day-60/day-90 due dates are calculated from `launch_date`. Missing
observations remain `unmeasured`; measured values are reported as `met` or `missed`. An opaque
`evidence_ref` is required when observations are recorded. The tool validates only its shape; it
does not authenticate the reference or verify that the underlying evidence supports the entered
values. The output is descriptive, not advice.

The founder must create and review a scorecard for every live product, select its thresholds, and
record decisions outside this utility. The presence of an example or a passing validation command
does not satisfy G2's live-product gate.

## G3 — aggregate founder-hours

```bash
python3 ops/scripts/teracorp_ops_metrics.py hours-trend \
  ops/metrics/examples/founder-hours.synthetic.json
```

Each row represents one Monday-start week. The only categories are approval, outage response, and
manual fulfilment minutes. Total category minutes may not exceed one week. To avoid false trends,
the report emits a four-week aggregate/change only when the latest four entries are consecutive;
missing weeks are never zero-filled. Keep source records in the founder-approved location and enter
aggregate minutes here, not names or incident/customer notes.

## Contract and limits

- Version 1 JSON contracts; unknown or missing keys fail closed.
- Product IDs and metric IDs are lowercase identifiers; evidence references are opaque and not URLs.
- Scorecard observations require a date on/after the relevant 60/90-day checkpoint and no later
  than the scorecard's explicit `as_of_date`.
- Input files are limited to 1 MiB and decimal strings to 64 characters to bound local parsing work.
- Founder-hours inputs accept at most 104 distinct Monday-start weeks; each category is an integer
  from 0 to 10,080 minutes, and the weekly category total cannot exceed 10,080 minutes.
- CLI reads only the explicit input file and writes JSON to stdout. It does not mutate that file.

Local tests: `uv run pytest -q deploys/tests/test_teracorp_ops_metrics.py`.

This prepares local tooling for G2/G3. Real product scorecards, actual weekly records, management
review, and the four-week trend remain required before either row is called operational.
