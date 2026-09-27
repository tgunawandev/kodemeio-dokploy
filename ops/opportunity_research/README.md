# Opportunity research evidence candidate (F0b / FC2)

This is an offline, local candidate for the F0b/FC2 evidence ledger. It accepts
only `.test` source domains and synthetic fixtures. It does not retrieve a
source, resolve an evidence reference, call VISION or another model, contact a
provider, or connect to Odoo. It is not a production integration and does not
promote or score opportunities.

Each opportunity has a bounded claim and SHA-256 digest, explicit evidence
references, a status, and an owner review record. Each evidence record has an
opaque reference, capture date, source domain and type, captured claim, and
digest. The contract rejects unknown fields. The validator rejects duplicate
evidence references, conflicting content under a repeated reference, duplicate
captures, orphan references, future-dated evidence and review dates, and common
email, phone, URL, and credential patterns. Errors identify fields and rule
codes only; submitted text is not echoed.

The source assertion status is always `unverified`. Owner review can leave a
candidate pending, mark it `reviewed_unverified`, or reject it. Review does not
make a source assertion verified. No confidence score or numeric threshold is
defined. The text pattern checks are a limited refusal layer, not a general
PII detector; this candidate must not be used with real or personal data.

Run the local example:

```sh
uv run python ops/opportunity_research/scripts/opportunity_research.py validate \
  ops/opportunity_research/examples/opportunity_research.synthetic.v1.json
```

Exit status is `0` for a structurally valid, still-unverified candidate and `1`
for refused input. Output is deterministic JSON. The schema and CLI are not
connected to a service, scheduled job, Hermes persona, model, provider, or
production path.
