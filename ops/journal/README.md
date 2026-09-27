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

- The event schema allows only machine IDs, approved action labels, timestamps, opaque record refs,
  and a SHA-256 digest of the action payload. It has no free-text description or raw arguments.
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
