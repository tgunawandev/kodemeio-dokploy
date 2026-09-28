# P8 — protected offsite action-journal rollout (founder-gated)

**Current state: do not schedule or claim P8 operational.** The repository has a local JSONL hash-chain
primitive and a one-shot exporter candidate. There is no scheduled production export job, no
authenticated Odoo/Hatchet/Hermes event producer, no approved production target, and no
restore/tamper drill. This runbook prepares the founder gates; it does not perform them.

## Required design decision

Use a dedicated private audit bucket/prefix in a separately administered offsite account/region.
Do not assume the existing general backup mirror key is immutable: Backblaze `writeFiles` includes
the `hide_file` operation, and the existing backup bucket lifecycle is not an Object Lock policy.
The read-only key must not write or change retention. The writer must be bucket/prefix-scoped and
must not have `deleteFiles`, `bypassGovernance`, `writeBucketRetentions`, or bucket administration.
If using per-object retention at upload time, grant only the file-retention capability required by
the selected client (`writeFileRetentions` in the B2 Native API capability model), and test that it
cannot shorten a compliance lock; do not grant governance-bypass.

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
3. Store the current expected journal head independently from the snapshot in a separately
   protected source (for example, a restricted audit control record). Supply that value as the
   exporter's `--expected-head`; never calculate the expected value from the snapshot being checked
   or accept a value copied from the same untrusted journal storage. A valid rebuilt hash chain
   must still fail when its head differs from this independent anchor.
4. The one-shot exporter uses conditional single-object writes (`If-None-Match: *`) and has no
   overwrite fallback. Before relying on it, the founder must prove the selected B2 S3 endpoint
   accepts the conditional request and refuses an existing object key. If B2 rejects or ignores
   the condition, export fails closed; do not remove the condition or fall back to an unconditional
   PUT. Record this provider-acceptance test as a founder drill gate.
5. The exporter reads each uploaded object back into a private bounded temporary file and compares
   exact byte count and SHA-256 (the snapshot digest is recorded in the manifest), in addition to
   checking retention metadata. Supply separate writer credentials and read-only credentials via
   `ACTION_JOURNAL_B2_ACCESS_KEY_ID`, `ACTION_JOURNAL_B2_SECRET_ACCESS_KEY`,
   `ACTION_JOURNAL_B2_READ_ACCESS_KEY_ID`, and `ACTION_JOURNAL_B2_READ_SECRET_ACCESS_KEY`. The read key must
   actually be provisioned read-only and from a separately governed trust boundary; distinct
   environment variables alone do not prove its capabilities.
6. Keep local journal and offsite keys separate. The runtime writer must not administer buckets,
   shorten retention, bypass governance, or delete versions. A scheduled production exporter,
   alerting, authenticated event producers, and operational ownership are still unimplemented;
   do not register a schedule yet.
7. Install a supported AWS CLI v2 executable at `/usr/local/bin/aws` or `/usr/bin/aws`; the
   exporter's child environment intentionally excludes user-local binary directories. Before an
   authorized test-bucket drill, verify the executable's `aws --version` output identifies
   `aws-cli/2.x`. The implementation uses the v2 `--no-cli-pager` option and must refuse if that
   exact executable path is unavailable.

## Synthetic acceptance before scheduling

Run the local tests from `kodemeio-dokploy`:

```sh
uv run pytest -q deploys/tests/test_action_journal_export.py
uv run ruff check ops/scripts/action_journal_export.py deploys/tests/test_action_journal_export.py
uv run ruff format --check ops/scripts/action_journal_export.py deploys/tests/test_action_journal_export.py
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
