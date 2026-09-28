# OPS1 founder weekly rhythm (manual procedure)

This is a lightweight operating cadence, not an automated agent workflow. Keep decisions and
approvals in their owning systems. Do not place customer details, credentials, or incident narratives
in the summary packet.

## Monday — priorities (founder-led)

1. Review open commitments, capacity, and the prior Friday numbers.
2. The founder writes the week's few priorities, owner (if any), and a plain-language completion
   condition in the normal work tracker. Set or change priorities by human judgment; the OPS1
   packet does not rank or recommend work.
3. Note what is explicitly deferred. Do not treat missing metrics as zero or as evidence for a
   business decision.

## Each working day — approval batch (20–30 minutes)

1. Open the relevant approval queue directly and confirm each item is in scope and has its required
   sanitized evidence/context.
2. Decide approve, reject, or hold in the owning system; record the reason there, not in this packet.
   If evidence or authorization is missing, hold and ask the responsible person.
3. Check that the owning system recorded the decision. OPS1 does not read the queue, make decisions,
   or execute approvals. Stop when the timebox ends; carry remaining items forward visibly.

## Friday — numbers and closeout

1. Export sanitized G2 product scorecard, G3 founder-hours, and G4 budget reconciliation summaries
   from their local tools, then assemble them with the OPS1 packet CLI. Use explicit files only.
2. Check each summary's freshness, gaps, and untrusted status. Follow up on source evidence in the
   source system; the assembler cannot authenticate evidence or prove invoice completeness. G4 is
   `fresh` for the latest closed month (age counted from the day after the month ends), `partial`
   for the still-open current month, and `stale` once an older month exceeds its declared age.
3. The founder records actual decisions and follow-ups in the normal work tracker. Note absent data
   as missing; do not infer a pivot, kill, continuation, or priority from the packet.
4. Retain a dated founder record for each completed week. Four consecutive records, reviewed by the
   founder, are required before claiming the G1 cadence has run. Synthetic examples and generated
   packets are not operating evidence.

## Local packet command

From the Dokploy repository, run:

```sh
python3 ops/scripts/teracorp_weekly_rhythm.py ops/metrics/ops1/examples/manifest.synthetic.json
```

The output goes to stdout. Inputs must be the JSON summary outputs of OPS2/OPS3/OPS4. Treat all
summaries as untrusted, even when a caller labels them founder-attested. Never point the CLI at
secrets, source-system exports, or non-sanitized records.
