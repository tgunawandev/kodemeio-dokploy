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
