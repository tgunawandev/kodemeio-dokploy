#!/usr/bin/env python3
"""Export a verified, minimized Teracorp journal to B2 with per-object COMPLIANCE retention.

This is a one-shot offline candidate. It never provisions a bucket or reads credential files.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import teracorp_action_journal as journal

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 64 * 1024
MAX_PREFIX_BYTES = 256
MAX_CREDENTIAL_BYTES = 1024
AWS_REGION = "us-east-1"
ACCESS_KEY_ENV = "TERACORP_B2_ACCESS_KEY_ID"
SECRET_KEY_ENV = "TERACORP_B2_SECRET_ACCESS_KEY"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{4,48}[a-z0-9]$")
_PREFIX_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,62}$")
_ENDPOINT_HOST = re.compile(r"^s3\.[a-z0-9]+-[a-z0-9]+-[0-9]{3}\.backblazeb2\.com$")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ExportError(ValueError):
    """Sanitized, caller-safe export validation or provider failure."""


Runner = Callable[[Sequence[str], Mapping[str, str], float], Any]
Clock = Callable[[], datetime]
RunIdFactory = Callable[[], str]


def validate_destination(bucket: str, prefix: str, endpoint: str, retention_days: int) -> None:
    if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
        raise ExportError("bucket name is invalid")
    if (
        not isinstance(prefix, str)
        or not prefix
        or len(prefix.encode("utf-8")) > MAX_PREFIX_BYTES
        or prefix.startswith("/")
        or prefix.endswith("/")
        or "//" in prefix
        or any(part in {".", ".."} or not _PREFIX_SEGMENT.fullmatch(part) for part in prefix.split("/"))
    ):
        raise ExportError("object prefix is invalid")
    if not isinstance(endpoint, str) or len(endpoint) > 256:
        raise ExportError("B2 endpoint is invalid")
    match = re.fullmatch(r"https://([^/:?#@]+)(?:/)?", endpoint)
    if match is None or not _ENDPOINT_HOST.fullmatch(match.group(1)):
        raise ExportError("B2 endpoint must be an HTTPS B2 S3 endpoint")
    if type(retention_days) is not int or not 1 <= retention_days <= 3000:
        raise ExportError("retention days must be an integer from 1 through 3000")


def _retention_date(days: int, clock: Clock) -> str:
    now = clock()
    if now.tzinfo is None:
        raise ExportError("clock must return a timezone-aware timestamp")
    # The extra second prevents truncating fractional seconds from shortening the selected duration.
    until = now.astimezone(UTC).replace(microsecond=0) + timedelta(days=days, seconds=1)
    return until.strftime(_UTC_FORMAT)


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ExportError("provider retention metadata is invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ExportError("provider retention metadata is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ExportError("provider retention metadata is invalid")
    return parsed.astimezone(UTC).replace(microsecond=0)


@contextmanager
def _locked_snapshot(journal_path: Path) -> Iterator[tuple[bytes, dict[str, Any]]]:
    """Hold the journal's writer lock across full verification and same-fd snapshot read."""
    lock_fd = journal._open_fd(Path(f"{journal_path}.lock"), os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_SH)
        snapshot_fd = journal._open_fd(journal_path, os.O_RDONLY)
        try:
            before = os.fstat(snapshot_fd)
            if before.st_size > MAX_SNAPSHOT_BYTES:
                raise ExportError("journal exceeds the exporter snapshot size limit")
            verified = journal._verify_fd(snapshot_fd)
            os.lseek(snapshot_fd, 0, os.SEEK_SET)
            pieces: list[bytes] = []
            remaining = MAX_SNAPSHOT_BYTES + 1
            while remaining:
                chunk = os.read(snapshot_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                pieces.append(chunk)
                remaining -= len(chunk)
            snapshot = b"".join(pieces)
            after = os.fstat(snapshot_fd)
            if len(snapshot) > MAX_SNAPSHOT_BYTES or len(snapshot) != before.st_size or after.st_size != before.st_size:
                raise ExportError("journal changed or exceeds the exporter snapshot size limit")
            yield snapshot, {"record_count": verified["record_count"], "head_hash": verified["head_hash"]}
        finally:
            os.close(snapshot_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _private_temp_file(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(content)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise ExportError("temporary snapshot write failed")
            remaining = remaining[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _child_environment(access_key: str, secret_key: str, home: Path) -> dict[str, str]:
    for value in (access_key, secret_key):
        if not isinstance(value, str) or not value or len(value) > MAX_CREDENTIAL_BYTES or "\x00" in value:
            raise ExportError("dedicated B2 credentials are missing or invalid")
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_DEFAULT_REGION": AWS_REGION,
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": "/dev/null",
        "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        "AWS_PAGER": "",
        "AWS_CLI_AUTO_PROMPT": "off",
    }


def _default_runner(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(env),
        timeout=timeout,
        shell=False,
        check=False,
    )


def _aws_call(
    operation: str,
    operation_args: Sequence[str],
    endpoint: str,
    env: Mapping[str, str],
    runner: Runner,
) -> dict[str, Any]:
    argv = [
        "aws",
        "--no-cli-pager",
        "--region",
        AWS_REGION,
        "--endpoint-url",
        endpoint,
        "--output",
        "json",
        "s3api",
        operation,
        *operation_args,
    ]
    try:
        completed = runner(argv, env, 60.0)
    except Exception:
        # Never expose exception strings: subprocess/provider errors can echo credentials or payloads.
        raise ExportError(f"B2 {operation} operation failed") from None
    if getattr(completed, "returncode", 1) != 0:
        raise ExportError(f"B2 {operation} operation failed")
    raw = getattr(completed, "stdout", "")
    if isinstance(raw, bytes):
        if len(raw) > MAX_PROVIDER_OUTPUT_BYTES:
            raise ExportError(f"B2 {operation} response exceeded its size limit")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ExportError(f"B2 {operation} response was invalid") from None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
        raise ExportError(f"B2 {operation} response exceeded its size limit")
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ExportError(f"B2 {operation} response was invalid") from None
    if type(result) is not dict:
        raise ExportError(f"B2 {operation} response was invalid")
    return result


def _assert_retention(actual_mode: Any, actual_date: Any, expected_date: str) -> None:
    if actual_mode != "COMPLIANCE" or _parse_utc(actual_date) != _parse_utc(expected_date):
        raise ExportError("B2 object retention does not match requested COMPLIANCE retention")


def _upload_and_verify(
    *,
    path: Path,
    bucket: str,
    key: str,
    endpoint: str,
    retain_until: str,
    env: Mapping[str, str],
    runner: Runner,
) -> None:
    put = _aws_call(
        "put-object",
        [
            "--bucket",
            bucket,
            "--key",
            key,
            "--body",
            str(path),
            "--object-lock-mode",
            "COMPLIANCE",
            "--object-lock-retain-until-date",
            retain_until,
        ],
        endpoint,
        env,
        runner,
    )
    _assert_retention(put.get("ObjectLockMode"), put.get("ObjectLockRetainUntilDate"), retain_until)
    if not isinstance(put.get("ETag"), str) or not put["ETag"]:
        raise ExportError("B2 upload response omitted object metadata")

    head = _aws_call("head-object", ["--bucket", bucket, "--key", key], endpoint, env, runner)
    if type(head.get("ContentLength")) is not int or head["ContentLength"] != path.stat().st_size:
        raise ExportError("B2 object size verification failed")
    if "ObjectLockMode" in head or "ObjectLockRetainUntilDate" in head:
        _assert_retention(head.get("ObjectLockMode"), head.get("ObjectLockRetainUntilDate"), retain_until)

    retention = _aws_call("get-object-retention", ["--bucket", bucket, "--key", key], endpoint, env, runner)
    value = retention.get("Retention")
    if type(value) is not dict:
        raise ExportError("B2 retention verification response was invalid")
    _assert_retention(value.get("Mode"), value.get("RetainUntilDate"), retain_until)


def export_snapshot(
    *,
    journal_path: Path,
    bucket: str,
    prefix: str,
    endpoint: str,
    retention_days: int,
    access_key: str,
    secret_key: str,
    runner: Runner = _default_runner,
    clock: Clock = lambda: datetime.now(UTC),
    run_id_factory: RunIdFactory = lambda: uuid.uuid4().hex,
) -> dict[str, Any]:
    validate_destination(bucket, prefix, endpoint, retention_days)
    if not isinstance(journal_path, Path):
        journal_path = Path(journal_path)
    run_id = run_id_factory()
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ExportError("run ID generator returned an invalid identifier")
    retain_until = _retention_date(retention_days, clock)
    snapshot_key = f"{prefix}/run-{run_id}/snapshot.jsonl"
    manifest_key = f"{prefix}/run-{run_id}/manifest.json"

    try:
        temp_context = tempfile.TemporaryDirectory(prefix="teracorp-journal-export-")
    except OSError:
        raise ExportError("private temporary workspace could not be created") from None
    with temp_context as temp_name:
        temp_dir = Path(temp_name)
        os.chmod(temp_dir, 0o700)
        try:
            with _locked_snapshot(journal_path) as locked:
                snapshot, state = locked
        except (OSError, journal.InputError, ExportError, RecursionError):
            raise ExportError("journal snapshot validation failed") from None

        # Re-verify the exact private snapshot bytes before any external call.
        snapshot_path = _private_temp_file(temp_dir, "snapshot.jsonl", snapshot)
        snapshot_fd = journal._open_fd(snapshot_path, os.O_RDONLY)
        try:
            copied_state = journal._verify_fd(snapshot_fd)
        except (OSError, journal.InputError):
            raise ExportError("private snapshot verification failed") from None
        finally:
            os.close(snapshot_fd)
        if copied_state["record_count"] != state["record_count"] or copied_state["head_hash"] != state["head_hash"]:
            raise ExportError("private snapshot verification failed")
        if stat.S_IMODE(snapshot_path.stat().st_mode) & 0o077:
            raise ExportError("private snapshot permissions are invalid")

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "bucket": bucket,
            "prefix": prefix,
            "snapshot_key": snapshot_key,
            "manifest_key": manifest_key,
            "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
            "snapshot_size_bytes": len(snapshot),
            "record_count": state["record_count"],
            "head_hash": state["head_hash"],
            "object_lock_mode": "COMPLIANCE",
            "retain_until": retain_until,
        }
        manifest_bytes = journal._canonical(manifest) + b"\n"
        manifest_path = _private_temp_file(temp_dir, "manifest.json", manifest_bytes)
        child_env = _child_environment(access_key, secret_key, temp_dir)

        _upload_and_verify(
            path=snapshot_path,
            bucket=bucket,
            key=snapshot_key,
            endpoint=endpoint,
            retain_until=retain_until,
            env=child_env,
            runner=runner,
        )
        _upload_and_verify(
            path=manifest_path,
            bucket=bucket,
            key=manifest_key,
            endpoint=endpoint,
            retain_until=retain_until,
            env=child_env,
            runner=runner,
        )
    return {
        "status": "verified",
        "run_id": run_id,
        "snapshot_key": snapshot_key,
        "manifest_key": manifest_key,
        "snapshot_size_bytes": len(snapshot),
        "record_count": state["record_count"],
        "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        "head_hash": state["head_hash"],
        "object_lock_mode": "COMPLIANCE",
        "retain_until": retain_until,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="private minimized action journal JSONL")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--endpoint", required=True, help="HTTPS B2 S3 endpoint URL")
    parser.add_argument("--retention-days", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        access_key = os.environ.get(ACCESS_KEY_ENV, "")
        secret_key = os.environ.get(SECRET_KEY_ENV, "")
        result = export_snapshot(
            journal_path=args.journal,
            bucket=args.bucket,
            prefix=args.prefix,
            endpoint=args.endpoint,
            retention_days=args.retention_days,
            access_key=access_key,
            secret_key=secret_key,
        )
    except (ExportError, OSError) as exc:
        # Messages are deliberately generic; no AWS output, local journal content, or credential data.
        message = str(exc) if isinstance(exc, ExportError) else "export failed"
        print(f"error: {message}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
