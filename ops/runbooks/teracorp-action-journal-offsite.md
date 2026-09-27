# P8 — protected offsite action-journal rollout (founder-gated)

**Current state: do not schedule or claim P8 operational.** The repository has a local JSONL hash-chain
primitive only. It has no authenticated Odoo/Hatchet/Hermes event producers, no snapshot/export job,
no production target, and no restore/tamper drill. This runbook prepares the founder gates; it does
not perform them.

## Required design decision

Use a dedicated private audit bucket/prefix in a separately administered offsite account/region.
Do not assume the existing general backup mirror key is immutable: Backblaze `writeFiles` includes
the `hide_file` operation, and the existing backup bucket lifecycle is not an Object Lock policy.
The read-only key must not write or change retention. The writer must be bucket/prefix-scoped and
must not have `deleteFiles`, `bypassGovernance`, `writeBucketRetentions`, or bucket administration.

For a genuinely protected copy, ask the founder and counsel to choose a retention period consistent
with the audit/data-retention policy, then use a dedicated bucket with Object Lock **compliance**
retention. Compliance retention cannot be shortened by any user; enabling Object Lock is
irreversible for that bucket. This is a founder decision, not a default to guess. On an already
Object-Lock-enabled bucket without default retention, every uploaded object must receive retention
explicitly. A no-delete key alone is not the same guarantee.

Backblaze documents Object Lock as preventing file modification/deletion until the retained-until
date. It documents compliance versus governance override semantics and the key capabilities for
retention. Review these primary sources at execution time:

- [Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock)
- [Object Lock with the S3-compatible API](https://www.backblaze.com/docs/cloud-storage-enable-object-lock-with-the-s3-compatible-api)
- [Application key capabilities](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities)

## Preconditions before building/enabling the scheduled export

1. Establish approved action sources and authenticated event producers. Never populate the journal
   from model text, a caller-asserted actor, or raw payloads. Keep only the minimized event schema;
   compute a digest of canonical source bytes without persisting the bytes.
2. Provision the dedicated bucket, encryption, compliance retention, and narrow writer/reader keys
   in the approved secrets manager. Do not put keys in Dokploy YAML, this repo, shell history, or an
   agent transcript.
3. Confirm the chosen client supports per-object compliance retention and protected unique object
   names. An upload that silently omits retention must fail acceptance.
4. Implement and review the scheduled exporter. It must first run the local full-chain verifier,
   export a consistent snapshot and trusted head metadata, upload content-addressed/new-only object
   names, verify remote size/checksum/retention metadata, and alert on any failed step. No such job
   currently exists, so do not register a schedule yet.
5. Keep local journal and offsite keys separate. The runtime writer must not administer buckets,
   shorten retention, bypass governance, or delete versions. The restore verifier uses a
   read-only key from a separate trust boundary.

## Synthetic acceptance before scheduling

Run the local tests from `kodemeio-dokploy`:

```sh
uv run pytest -q deploys/tests/test_teracorp_action_journal.py
uv run ruff check ops/scripts/teracorp_action_journal.py deploys/tests/test_teracorp_action_journal.py
uv run ruff format --check ops/scripts/teracorp_action_journal.py deploys/tests/test_teracorp_action_journal.py
```

These prove only the local component. Once a founder provisions a dedicated test bucket and
explicitly authorizes an external write, use a **synthetic non-personal event** and a short,
founder-approved test retention period to prove all of the following: an object was uploaded; the
remote metadata shows compliance retention; hard-delete and overwrite attempts are refused until
expiry; a read-only key can fetch and verify the snapshot; a deliberately altered local copy fails
the chain/head check; and a missing/old snapshot causes a monitored failure. A locked object cannot
be cleaned up early; choose the test prefix/retention accordingly and wait for expiry. Do not test on
the production audit bucket before this isolated proof passes.

## Restore / tamper drill and evidence

After production integration is implemented and approved, the founder must record a dated drill
showing: selected remote snapshot and retention metadata; independent head value; restored snapshot
verification; a tampered/truncated copy refused; object-retention delete/overwrite refusal; alert
delivery for a missing export; measured RPO; operator and independent verifier. Store source evidence
in the restricted audit location and put only opaque references in progress records.

**P8 remains not operational** until source-authenticated actions flow into the journal, the locked
offsite exporter succeeds on schedule, remote retention and least privilege are verified, and a
restore/tamper drill passes. No credentials, account setup, network write, deployment, or live event
was performed to write this runbook.
