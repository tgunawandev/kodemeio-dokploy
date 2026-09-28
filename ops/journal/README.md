# Teracorp P8 — local action-journal primitive

This is a local, append-only JSONL journal primitive with a SHA-256 hash chain. It is **not yet
integrated with Odoo, Hatchet, Hermes, approvals, or any production action source**. Events must
currently be explicitly supplied as minimized JSON metadata.

```bash
python3 ops/scripts/teracorp_action_journal.py append \
  /var/lib/teracorp/actions.jsonl ops/journal/examples/action.synthetic.json
python3 ops/scripts/teracorp_action_journal.py verify /var/lib/teracorp/actions.jsonl
python3 ops/scripts/teracorp_action_journal.py verify /var/lib/teracorp/actions.jsonl \
  --expected-head '<head-digest-held-separately>'
```

The CLI currently requires a POSIX platform with `fcntl.flock` (the production estate is Linux).
`append` takes a process lock, refuses symlink/non-regular or group/world-accessible journal files,
validates the full existing chain, refuses duplicate event IDs, appends a canonical record, and
fsyncs the file and containing directory. `verify` checks each sequence number, canonical encoding,
event schema, unique ID, prior hash, and entry hash. The journal is capped at 100,000 records / 256
MiB; records at 32 KiB; event input at 16 KiB. Rotate only under an approved retention procedure
that preserves an independently trusted prior head.

## Security boundary

- The event schema allows only prefixed UUID-shaped identifiers, fixed model/reference namespaces,
  approved action labels, timestamps, and a SHA-256 digest of the action payload. The identifier
  shapes keep names and free text out of accepted values; they are not authentication or PII
  detection. Producers must derive IDs from trusted internal records and must never put names,
  email addresses, phone numbers, prompts, raw arguments, tokens, credentials, or financial content
  into any field. The schema has no free-text description.
- Actor and tenant identifiers are caller claims; this tool does not authenticate them or authorize
  actions. The payload digest is only format-checked; this tool does not compute it, reveal it, or
  validate its preimage.
- A hash chain alone is not immutable storage. A privileged writer can rebuild it. Copy head hashes
  to a separately administered immutable/offsite location (for example, an approved object-lock
  target) and pass the independently recorded digest with `--expected-head`. This repo does not
  provision or verify that offsite target.
- The included example is synthetic. Do not record customer/person names, prompts, raw arguments,
  tokens, credentials, or personal/financial content.

## Acceptance boundary

Tests cover deterministic chained appends, concurrent writers, tamper detection, external anchors,
truncation/symlink refusal, strict event schemas, permissions, and the absence of network/database
dependencies. This is local component evidence only. Production integration, authenticated event
producers, an immutable offsite anchor, and a restore/tamper drill are required before P8 is
operational or production-ready.

## B2 Object Lock export candidate (offline code only)

`ops/scripts/teracorp_action_journal_export.py` is a separate, explicit one-shot export candidate.
It locks the existing journal against cooperating appenders while verifying and reading a bounded
snapshot, then requests COMPLIANCE retention for a snapshot and canonical manifest under a random
run prefix. It verifies each `put-object` response, `head-object` size and
`get-object-retention` result. Tests use only an injected fake AWS CLI runner; no bucket, credential,
production data, scheduled job, or deployment is configured here.

The command requires a pre-existing bucket and an explicit B2 endpoint/prefix/retention duration.
The two credentials must be injected into the process environment under dedicated names; it does
not read local AWS files or general `AWS_*` credential variables:

```bash
python3 ops/scripts/teracorp_action_journal_export.py /var/lib/teracorp/actions.jsonl \
  --bucket <founder-provisioned-bucket> \
  --prefix <dedicated-safe-prefix> \
  --endpoint https://s3.us-west-004.backblazeb2.com \
  --retention-days <approved-1-to-3000> \
  --expected-head <trusted-current-head-sha256>
```

The expected head must come from a separately protected source, not the journal being checked.

For unattended nightly runs, anchor each export on the previous run's manifest instead of (or in
addition to) `--expected-head`:

```bash
python3 ops/scripts/teracorp_action_journal_export.py /var/lib/teracorp/actions.jsonl \
  --bucket <founder-provisioned-bucket> --prefix <dedicated-safe-prefix> \
  --endpoint https://s3.us-west-004.backblazeb2.com --retention-days <approved-1-to-3000> \
  --anchor-manifest <previous-run>.manifest.json \
  --manifest-out <this-run>.manifest.json
```

The chain must reproduce the anchor's `(record_count, head_hash)` at that sequence and may only
append after it; a rewritten or truncated record at or before the anchor is refused before any
provider call. The anchor must name the same bucket and prefix. `--manifest-out` writes the exact
uploaded manifest (new file, mode 0600) only after both objects verified, so it becomes the next
anchor; each manifest also records the `anchor` it continued from. The first run, or a run after a
lost local manifest, needs `--expected-head` (or a manifest read back from the Object-Lock bucket).
`put-object`/`get-object` timeouts scale with object size (60 s plus 1 s per 256 KiB, capped at
one hour); other provider calls keep 60 s.
Required environment names: `TERACORP_B2_ACCESS_KEY_ID`, `TERACORP_B2_SECRET_ACCESS_KEY`,
`TERACORP_B2_READ_ACCESS_KEY_ID`, and `TERACORP_B2_READ_SECRET_ACCESS_KEY`; the read-only
credentials must be independently provisioned with read-only capabilities. Do not paste
credentials into the command line or commit them.
The child process receives only a sanitized environment with those explicit credentials and fixed
AWS CLI settings; output and error streams from AWS are never printed. The key must be scoped by the
founder to this bucket/prefix and must lack delete and bucket-administration capabilities. The
bucket must already have Object Lock enabled; this candidate never creates or changes bucket
settings.

**Offline candidate only—not operational or production-ready.** The founder must provision and
review the compliance bucket/key and retention/legal implications, then authorize a synthetic
upload and complete readback, tamper-detection and restore drills with captured evidence. No such
live action is part of this implementation.
