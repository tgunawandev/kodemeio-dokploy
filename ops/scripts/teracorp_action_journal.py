#!/usr/bin/env python3
"""Append and verify a local, privacy-minimized Teracorp action journal.

This is a local journal primitive, not an application integration or trusted identity provider.
Hashes make accidental/unauthorized edits detectable relative to a protected external head anchor;
they do not authenticate actors or prevent a storage administrator from rebuilding the chain.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_EVENT_BYTES = 16_384
_MAX_RECORD_BYTES = 32_768
_MAX_RECORDS = 100_000
_MAX_JOURNAL_BYTES = 256 * 1024 * 1024
_GENESIS = "0" * 64
_ACTIONS = {
    "workflow.requested",
    "approval.granted",
    "approval.rejected",
    "action.started",
    "action.completed",
    "action.failed",
    "action.cancelled",
}


class InputError(ValueError):
    """Input, journal contents, or filesystem target violates the local contract."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        unknown = sorted(set(value) - keys)
        raise InputError(f"{where} keys invalid (missing={missing}, unknown={unknown})")
    return value


def _safe_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InputError(f"{where} must be a safe lowercase machine identifier")
    return value


def _reference(value: Any, where: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _REF.fullmatch(value) or "://" in value:
        raise InputError(f"{where} must be an opaque reference, not a URL or free text")
    return value


def validate_event(value: Any) -> dict[str, Any]:
    event = _object(
        value,
        "event",
        {
            "schema_version",
            "event_id",
            "occurred_at",
            "tenant_id",
            "actor_kind",
            "actor_id",
            "action",
            "work_order_ref",
            "target_ref",
            "approval_ref",
            "payload_sha256",
        },
    )
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        raise InputError("event.schema_version must be integer 1")
    for field in ("event_id", "tenant_id", "actor_id"):
        _safe_id(event[field], f"event.{field}")
    actor_kind = event["actor_kind"]
    if not isinstance(actor_kind, str) or actor_kind not in {"human", "agent", "service"}:
        raise InputError("event.actor_kind must be human, agent, or service")
    action = event["action"]
    if not isinstance(action, str) or action not in _ACTIONS:
        raise InputError("event.action is not in the journal action allowlist")
    occurred_at = event["occurred_at"]
    if not isinstance(occurred_at, str) or not _UTC.fullmatch(occurred_at):
        raise InputError("event.occurred_at must be canonical UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed_at = datetime.strptime(occurred_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise InputError("event.occurred_at must be a valid UTC timestamp") from exc
    if parsed_at.strftime("%Y-%m-%dT%H:%M:%SZ") != occurred_at:
        raise InputError("event.occurred_at must be canonical UTC YYYY-MM-DDTHH:MM:SSZ")
    _reference(event["work_order_ref"], "event.work_order_ref")
    _reference(event["target_ref"], "event.target_ref")
    _reference(event["approval_ref"], "event.approval_ref", optional=True)
    digest = event["payload_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise InputError("event.payload_sha256 must be a lowercase SHA-256 digest")
    return event


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _hash(sequence: int, event: dict[str, Any], previous_hash: str) -> str:
    body = {"sequence": sequence, "event": event, "previous_hash": previous_hash}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _open_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags | nofollow, mode)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InputError("journal and lock targets must not be symlinks") from exc
        raise
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise InputError("journal and lock targets must be regular files")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.close(fd)
        raise InputError("journal and lock files must not be group/world accessible")
    return fd


def _verify_fd(fd: int) -> dict[str, Any]:
    if os.fstat(fd).st_size > _MAX_JOURNAL_BYTES:
        raise InputError(f"journal exceeds {_MAX_JOURNAL_BYTES} bytes; rotate only after preserving a trusted anchor")
    os.lseek(fd, 0, os.SEEK_SET)
    count = 0
    previous = _GENESIS
    event_ids: set[str] = set()
    with os.fdopen(os.dup(fd), "rb") as stream:
        while True:
            line = stream.readline(_MAX_RECORD_BYTES + 1)
            if not line:
                break
            if len(line) > _MAX_RECORD_BYTES:
                raise InputError("journal record exceeds the maximum record size")
            if not line.endswith(b"\n"):
                raise InputError("journal must contain only newline terminated records")
            try:
                record = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise InputError("journal contains invalid JSON") from exc
            record = _object(record, "journal record", {"sequence", "event", "previous_hash", "entry_hash"})
            event = validate_event(record["event"])
            count += 1
            if count > _MAX_RECORDS:
                raise InputError(f"journal exceeds {_MAX_RECORDS} records")
            if type(record["sequence"]) is not int or record["sequence"] != count:
                raise InputError("journal sequence is not contiguous")
            if record["previous_hash"] != previous:
                raise InputError("journal previous_hash chain mismatch")
            if event["event_id"] in event_ids:
                raise InputError("journal event_id values must be unique")
            event_ids.add(event["event_id"])
            expected = _hash(count, event, previous)
            if record["entry_hash"] != expected:
                raise InputError("journal entry hash mismatch")
            if line != _canonical(record) + b"\n":
                raise InputError("journal record is not canonically encoded")
            previous = expected
    return {"record_count": count, "head_hash": previous, "event_ids": event_ids}


def _inspect_journal(path: Path) -> dict[str, Any]:
    try:
        fd = _open_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        return {"record_count": 0, "head_hash": _GENESIS, "event_ids": set()}
    try:
        return _verify_fd(fd)
    finally:
        os.close(fd)


def verify_journal(path: Path, *, expected_head: str | None = None) -> dict[str, Any]:
    """Verify the entire chain and optionally compare its head to a separately protected anchor."""
    if expected_head is not None and (not isinstance(expected_head, str) or not _SHA256.fullmatch(expected_head)):
        raise InputError("expected_head must be a lowercase SHA-256 digest")
    result = _inspect_journal(path)
    if expected_head is not None and result["head_hash"] != expected_head:
        raise InputError("journal head does not match external expected head")
    return {"record_count": result["record_count"], "head_hash": result["head_hash"]}


def append_event(path: Path, value: Any) -> dict[str, Any]:
    """Validate and append one event under a process lock, then fsync before returning."""
    event = validate_event(value)
    lock_path = Path(f"{path}.lock")
    lock_fd = _open_fd(lock_path, os.O_CREAT | os.O_WRONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _inspect_journal(path)
        if state["record_count"] >= _MAX_RECORDS:
            raise InputError(f"journal exceeds {_MAX_RECORDS} records")
        if event["event_id"] in state["event_ids"]:
            raise InputError("event_id values must be unique")
        sequence = state["record_count"] + 1
        previous = state["head_hash"]
        entry_hash = _hash(sequence, event, previous)
        record = {"sequence": sequence, "event": event, "previous_hash": previous, "entry_hash": entry_hash}
        encoded = _canonical(record) + b"\n"
        if len(encoded) > _MAX_RECORD_BYTES:
            raise InputError("journal record exceeds the maximum record size")
        journal_fd = _open_fd(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        try:
            if os.fstat(journal_fd).st_size + len(encoded) > _MAX_JOURNAL_BYTES:
                raise InputError(f"journal would exceed {_MAX_JOURNAL_BYTES} bytes")
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(journal_fd, remaining)
                if written <= 0:
                    raise OSError("short journal write")
                remaining = remaining[written:]
            os.fsync(journal_fd)
        finally:
            os.close(journal_fd)
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent or Path("."), dir_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {"sequence": sequence, "previous_hash": previous, "entry_hash": entry_hash}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_event(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_EVENT_BYTES + 1)
    if len(raw) > _MAX_EVENT_BYTES:
        raise InputError(f"event input must not exceed {_MAX_EVENT_BYTES} bytes")
    try:
        return validate_event(json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError("event input must be valid UTF-8 JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    append = commands.add_parser("append", help="append one validated event to the local journal")
    append.add_argument("journal", type=Path)
    append.add_argument("event", type=Path)
    verify = commands.add_parser("verify", help="verify the chain and optional external head anchor")
    verify.add_argument("journal", type=Path)
    verify.add_argument("--expected-head", help="head digest stored separately in protected offsite storage")
    args = parser.parse_args(argv)
    try:
        result = (
            append_event(args.journal, _load_event(args.event))
            if args.command == "append"
            else verify_journal(args.journal, expected_head=args.expected_head)
        )
    except (OSError, InputError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
