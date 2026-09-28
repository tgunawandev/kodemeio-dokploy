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
import resource
import selectors
import stat
import subprocess
import sys
import tempfile
import time
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
MAX_ANCHOR_MANIFEST_BYTES = 64 * 1024
BASE_PROVIDER_TIMEOUT_SECONDS = 60.0
# Transfers get one extra second per 256 KiB (i.e. they tolerate links down to ~256 KiB/s).
TRANSFER_BYTES_PER_EXTRA_SECOND = 256 * 1024
MAX_TRANSFER_TIMEOUT_SECONDS = 3600.0
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "bucket",
        "prefix",
        "snapshot_key",
        "manifest_key",
        "snapshot_sha256",
        "snapshot_size_bytes",
        "record_count",
        "head_hash",
        "object_lock_mode",
        "retain_until",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
AWS_REGION = "us-east-1"
ACCESS_KEY_ENV = "TERACORP_B2_ACCESS_KEY_ID"
SECRET_KEY_ENV = "TERACORP_B2_SECRET_ACCESS_KEY"
READ_ACCESS_KEY_ENV = "TERACORP_B2_READ_ACCESS_KEY_ID"
READ_SECRET_KEY_ENV = "TERACORP_B2_READ_SECRET_ACCESS_KEY"
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


def _transfer_timeout(size_bytes: int) -> float:
    """Scale the provider timeout for object transfers so large valid snapshots do not time out."""
    extra = max(0, size_bytes) // TRANSFER_BYTES_PER_EXTRA_SECOND
    return min(BASE_PROVIDER_TIMEOUT_SECONDS + float(extra), MAX_TRANSFER_TIMEOUT_SECONDS)


def _anchor_reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExportError("anchor manifest is invalid")
        result[key] = value
    return result


def load_anchor_manifest(path: Path) -> dict[str, Any]:
    """Read the previous run's local manifest copy (bounded, no symlinks, strict JSON)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise ExportError("anchor manifest could not be read") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ExportError("anchor manifest could not be read")
        raw = os.read(fd, MAX_ANCHOR_MANIFEST_BYTES + 1)
    except OSError:
        raise ExportError("anchor manifest could not be read") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_ANCHOR_MANIFEST_BYTES:
        raise ExportError("anchor manifest exceeds its size limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_anchor_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ExportError("anchor manifest is invalid") from None
    if type(value) is not dict:
        raise ExportError("anchor manifest is invalid")
    return value


def _anchor_from_manifest(manifest: Any, bucket: str, prefix: str) -> dict[str, Any]:
    """Extract ``(record_count, head_hash)`` from a previous export manifest for this destination."""
    if type(manifest) is not dict or set(manifest) not in (set(_MANIFEST_KEYS), set(_MANIFEST_KEYS) | {"anchor"}):
        raise ExportError("anchor manifest is invalid")
    if manifest["schema_version"] != 1 or type(manifest["schema_version"]) is not int:
        raise ExportError("anchor manifest is invalid")
    if manifest["bucket"] != bucket or manifest["prefix"] != prefix:
        raise ExportError("anchor manifest belongs to a different export destination")
    if manifest["object_lock_mode"] != "COMPLIANCE":
        raise ExportError("anchor manifest is invalid")
    count, head = manifest["record_count"], manifest["head_hash"]
    if type(count) is not int or count < 0 or not isinstance(head, str) or not _SHA256.fullmatch(head):
        raise ExportError("anchor manifest is invalid")
    return {"record_count": count, "head_hash": head}


def _chain_hash_at(snapshot: bytes, record_count: int) -> str | None:
    """Return the verified chain head after ``record_count`` records, or None if the chain is shorter."""
    if record_count == 0:
        return journal._GENESIS
    lines = snapshot.split(b"\n", record_count)
    if len(lines) < record_count or (len(lines) == record_count and not lines[-1]):
        return None
    record = json.loads(lines[record_count - 1])
    return record["entry_hash"] if record.get("sequence") == record_count else None


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
    command = list(argv)
    download_path = Path(command[-1]) if "s3api" in command and "get-object" in command else None
    download_limit = MAX_SNAPSHOT_BYTES

    def enforce_download_limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (download_limit, download_limit))

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            shell=False,
            bufsize=0,
            preexec_fn=enforce_download_limit if download_path is not None else None,
        )
    except OSError:
        raise RuntimeError("provider process could not be started") from None

    captured = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout

    def stop_child() -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    try:
        with selectors.DefaultSelector() as selector:
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                assert stream is not None
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    stop_child()
                    raise subprocess.TimeoutExpired(command, timeout)
                if (
                    download_path is not None
                    and download_path.exists()
                    and download_path.stat().st_size > download_limit
                ):
                    stop_child()
                    raise RuntimeError("provider download exceeded its size limit")
                for key, _ in selector.select(min(remaining_time, 0.1)):
                    name = key.data
                    remaining_bytes = MAX_PROVIDER_OUTPUT_BYTES - len(captured[name])
                    chunk = os.read(key.fd, min(8192, remaining_bytes + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(chunk) > remaining_bytes:
                        stop_child()
                        raise RuntimeError("provider output limit exceeded")
                    captured[name].extend(chunk)
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        stop_child()
        raise
    finally:
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            if stream is not None:
                stream.close()
    if download_path is not None and download_path.exists() and download_path.stat().st_size > download_limit:
        raise RuntimeError("provider download exceeded its size limit")
    return subprocess.CompletedProcess(
        command,
        returncode,
        captured["stdout"].decode("utf-8", errors="replace"),
        captured["stderr"].decode("utf-8", errors="replace"),
    )


def _aws_call(
    operation: str,
    operation_args: Sequence[str],
    endpoint: str,
    env: Mapping[str, str],
    runner: Runner,
    timeout: float = BASE_PROVIDER_TIMEOUT_SECONDS,
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
        completed = runner(argv, env, timeout)
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
    read_env: Mapping[str, str],
    runner: Runner,
    expected_sha256: str,
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
            "--if-none-match",
            "*",
            "--object-lock-mode",
            "COMPLIANCE",
            "--object-lock-retain-until-date",
            retain_until,
        ],
        endpoint,
        env,
        runner,
        _transfer_timeout(path.stat().st_size),
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

    download_path = path.parent / f"readback-{uuid.uuid4().hex}.bin"
    _aws_call(
        "get-object",
        ["--bucket", bucket, "--key", key, str(download_path)],
        endpoint,
        read_env,
        runner,
        _transfer_timeout(path.stat().st_size),
    )
    try:
        info = download_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_BYTES:
            raise ExportError("B2 readback exceeded its size limit")
        os.chmod(download_path, 0o600)
        digest = hashlib.sha256()
        size = 0
        readback_fd = os.open(download_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(readback_fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_SNAPSHOT_BYTES:
                raise ExportError("B2 readback exceeded its size limit")
            remote = os.fdopen(readback_fd, "rb")
            readback_fd = -1
            with remote:
                while chunk := remote.read(1024 * 1024):
                    size += len(chunk)
                    if size > path.stat().st_size:
                        raise ExportError("B2 readback verification failed")
                    digest.update(chunk)
        finally:
            if readback_fd >= 0:
                os.close(readback_fd)
        if size != path.stat().st_size or digest.hexdigest() != expected_sha256:
            raise ExportError("B2 readback verification failed")
    except OSError:
        raise ExportError("B2 readback verification failed") from None
    finally:
        download_path.unlink(missing_ok=True)


def export_snapshot(
    *,
    journal_path: Path,
    bucket: str,
    prefix: str,
    endpoint: str,
    retention_days: int,
    access_key: str,
    secret_key: str,
    read_access_key: str,
    read_secret_key: str,
    expected_head: str | None = None,
    anchor_manifest: Mapping[str, Any] | None = None,
    manifest_out: Path | None = None,
    runner: Runner = _default_runner,
    clock: Clock = lambda: datetime.now(UTC),
    run_id_factory: RunIdFactory = lambda: uuid.uuid4().hex,
) -> dict[str, Any]:
    """Export a verified snapshot.

    Trust comes from ``expected_head`` (an independently held current head), from
    ``anchor_manifest`` (the previous run's manifest: the chain must reproduce its
    ``(record_count, head_hash)`` and may only append after it), or from both.
    ``manifest_out`` saves this run's manifest locally to anchor the next run.
    """
    validate_destination(bucket, prefix, endpoint, retention_days)
    if expected_head is None and anchor_manifest is None:
        raise ExportError("an expected journal head or a previous export anchor manifest is required")
    if expected_head is not None and (not isinstance(expected_head, str) or not _SHA256.fullmatch(expected_head)):
        raise ExportError("expected journal head must be a lowercase SHA-256 digest")
    anchor = None if anchor_manifest is None else _anchor_from_manifest(dict(anchor_manifest), bucket, prefix)
    if manifest_out is not None:
        manifest_out = Path(manifest_out)
        if os.path.lexists(manifest_out):
            raise ExportError("manifest output path already exists")
    if (access_key, secret_key) == (read_access_key, read_secret_key):
        raise ExportError("B2 writer and read-only credentials must be distinct")
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
        if expected_head is not None and state["head_hash"] != expected_head:
            raise ExportError("journal head does not match independent expected journal head")
        if anchor is not None and (
            anchor["record_count"] > state["record_count"]
            or _chain_hash_at(snapshot, anchor["record_count"]) != anchor["head_hash"]
        ):
            raise ExportError("journal chain does not continue from the previous export anchor")

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
            "anchor": anchor,
        }
        manifest_bytes = journal._canonical(manifest) + b"\n"
        manifest_path = _private_temp_file(temp_dir, "manifest.json", manifest_bytes)
        child_env = _child_environment(access_key, secret_key, temp_dir)
        read_child_env = _child_environment(read_access_key, read_secret_key, temp_dir)

        _upload_and_verify(
            path=snapshot_path,
            bucket=bucket,
            key=snapshot_key,
            endpoint=endpoint,
            retain_until=retain_until,
            env=child_env,
            read_env=read_child_env,
            runner=runner,
            expected_sha256=manifest["snapshot_sha256"],
        )
        _upload_and_verify(
            path=manifest_path,
            bucket=bucket,
            key=manifest_key,
            endpoint=endpoint,
            retain_until=retain_until,
            env=child_env,
            read_env=read_child_env,
            runner=runner,
            expected_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )
    if manifest_out is not None:
        try:
            _private_temp_file(manifest_out.parent, manifest_out.name, manifest_bytes)
        except OSError:
            raise ExportError("manifest output could not be written after a verified export") from None
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
    parser.add_argument("--expected-head", help="independent protected journal head SHA-256")
    parser.add_argument(
        "--anchor-manifest",
        type=Path,
        help="previous run's manifest; the chain must continue from its record_count/head_hash",
    )
    parser.add_argument("--manifest-out", type=Path, help="write this run's manifest here (new file, 0600)")
    args = parser.parse_args(argv)
    try:
        access_key = os.environ.get(ACCESS_KEY_ENV, "")
        secret_key = os.environ.get(SECRET_KEY_ENV, "")
        read_access_key = os.environ.get(READ_ACCESS_KEY_ENV, "")
        read_secret_key = os.environ.get(READ_SECRET_KEY_ENV, "")
        anchor_manifest = None if args.anchor_manifest is None else load_anchor_manifest(args.anchor_manifest)
        result = export_snapshot(
            journal_path=args.journal,
            bucket=args.bucket,
            prefix=args.prefix,
            endpoint=args.endpoint,
            retention_days=args.retention_days,
            access_key=access_key,
            secret_key=secret_key,
            read_access_key=read_access_key,
            read_secret_key=read_secret_key,
            expected_head=args.expected_head,
            anchor_manifest=anchor_manifest,
            manifest_out=args.manifest_out,
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
