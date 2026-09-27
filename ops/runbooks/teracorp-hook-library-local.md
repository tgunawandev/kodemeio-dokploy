# Teracorp hook library — local/offline use only

This F7 artifact validates typed hook metadata and aggregate performance windows. It does not
generate copy, ingest a platform, rank content, retire a hook, publish/post, spend, or change an
agent. Do not place customer identifiers/events, raw platform payloads, credentials, URLs, or
free-form customer text in the document.

## Validate the bundled synthetic example

From the `kodemeio-dokploy` repository root:

```sh
python3 ops/scripts/teracorp_hook_library.py \
  --input ops/examples/teracorp_hook_library.synthetic.v1.json
```

The example is explicitly synthetic. Its checksums are fixture placeholders; the output labels its
window `synthetic_sample_not_real_outcome`. It is not evidence of Teracorp performance.

## Input and decisions

See `ops/contracts/teracorp_hook_library.v1.schema.json` and
`ops/contracts/teracorp_hook_performance_window.v1.schema.json`. Supply `as_of_date` explicitly
for deterministic freshness (30 days maximum). Missing data stays missing; it is not a zero.
Identical window IDs with identical content are collapsed; conflicting replays and overlapping
windows are refused.

Retirement is not inferred. If a founder supplies an explicit `retire`/`retain` decision, include
the exact approved threshold fields, at least two fresh verified real windows for retirement, and
same-brand verified evidence references for the measurement, threshold approval, and founder
approval. The tool validates the supplied record but does not modify the hook lifecycle. Local
checks validate evidence structure/linkage only: they do not authenticate the founder, verify the
source system, or prove that the supplied digest is independently trustworthy. Never use synthetic
data for a retirement decision.

No live source, external API, secret, network connection, Odoo service, deployment, or push is used
by this utility. A future integration requires a separate reviewed design and founder approval.
