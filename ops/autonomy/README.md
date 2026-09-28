# Teracorp A10 — offline autonomy evidence packet

`python3 ops/scripts/teracorp_autonomy_evidence.py ops/autonomy/evidence.synthetic.json`
validates a bounded local evidence package against the checked-in approval policy. The report can
only be `evidence_insufficient`, `always_human_refused`, `already_autonomous_noop`,
`not_promotable_refused`, or `candidate_for_manual_review_unverified`.

Candidate classes come from the versioned `contracts/approvals/autonomy.v1.yaml` `promotable`
list (today: `operational`). Classes already auto-approvable (`draft`) are refused as a no-op;
`never auto` classes (`financial`, `admin`) and every `always_human` class are refused. The contract
is validated against `policy.v1.yaml` so it can never make a forbidden class promotable.

This is not an autonomy engine or approval system. It never edits policy or executes an action.
Evaluation runs, incident counts and thresholds are caller claims; the CLI cannot authenticate their
source or prove that runs are complete. A candidate report is therefore not an eligibility decision.
Founder review, independently authenticated evidence, and a separately signed policy change are
required before any runtime behavior can change. The existing always-human list is an unconditional
refusal boundary.

The synthetic sample's thresholds illustrate input shape only. They are not recommendations or
approved Teracorp policy.

## Founder-signed grants (policy change path)

`ops/scripts/teracorp_autonomy_grant.py` is the only path by which a class leaves human approval.
A grant (`contracts/approvals/autonomy-grant.v1.schema.json`) names one class, the evidence packet
digest, the profiles it applies to, and an issue/expiry window (at most `max_grant_days`). It is
honoured only with a valid Ed25519 signature by a key in `autonomy.v1.yaml` `trust_roots`; any
edited field, unknown or wrong key, missing signature, expiry, always-human, never-auto or
already-autonomous class refuses it. `AutonomyRuntime.decide()` is the runtime consumer: it refuses
`always_human` first, re-checks expiry on every decision, and promotes exactly the granted class in
the granted profiles.

```sh
uv run python ops/scripts/teracorp_autonomy_grant.py digest ops/autonomy/evidence.synthetic.json
uv run python ops/scripts/teracorp_autonomy_grant.py sign grant.json --key-file ~/founder.key --key-id <id>   # founder, offline
uv run python ops/scripts/teracorp_autonomy_grant.py verify signed.json --evidence ops/autonomy/evidence.synthetic.json
```

`trust_roots` is empty in the checked-in contract, so every grant is refused until the founder
enrols a public key (operational). Tests use deterministic synthetic keys only. Wiring the same
decision into Odoo `mcp_base` classify, key custody and rotation, and an authenticated evaluation
store are operational follow-ups.
