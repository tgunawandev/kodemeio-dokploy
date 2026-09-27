from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "ops/scripts"
sys.path.insert(0, str(SCRIPTS))
import teracorp_action_journal as JOURNAL  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "teracorp_action_journal_export", SCRIPTS / "teracorp_action_journal_export.py"
)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def event(event_id: str = "evt-export") -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "occurred_at": "2026-09-28T08:30:00Z",
        "tenant_id": "kodemeio",
        "actor_kind": "agent",
        "actor_id": "karen",
        "action": "workflow.requested",
        "work_order_ref": "odoo:work.order/123",
        "target_ref": "factory:template/terakidz-kit",
        "approval_ref": None,
        "payload_sha256": "a" * 64,
    }


def fixed_clock() -> datetime:
    return datetime(2026, 9, 28, 1, 2, 3, 456789, tzinfo=UTC)


def runner_for(*, fail_operation: str | None = None, retention_mode: str = "COMPLIANCE"):
    calls: list[tuple[list[str], dict[str, str]]] = []
    temp_paths: list[Path] = []

    def runner(argv, env, timeout):
        assert isinstance(argv, list)
        assert timeout == 60.0
        calls.append((argv, dict(env)))
        operation = argv[argv.index("s3api") + 1]
        if operation == fail_operation:
            return SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE_PROVIDER_ERROR secret-value")
        bucket = argv[argv.index("--bucket") + 1]
        key = argv[argv.index("--key") + 1]
        assert bucket == "teracorp-backup"
        assert key.startswith("journal/offsite/run-")
        if operation == "put-object":
            file_path = Path(argv[argv.index("--body") + 1])
            temp_paths.append(file_path)
            assert os.stat(file_path.parent).st_mode & 0o777 == 0o700
            assert os.stat(file_path).st_mode & 0o777 == 0o600
            assert argv[argv.index("--object-lock-mode") + 1] == "COMPLIANCE"
            retention = argv[argv.index("--object-lock-retain-until-date") + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"ETag": '"opaque-etag"', "ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": retention}
                ),
                stderr="",
            )
        if operation == "head-object":
            # Calls are sequenced put -> head -> retention for each object.
            upload_path = temp_paths[1] if len(calls) >= 5 else temp_paths[0]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ContentLength": upload_path.stat().st_size}),
                stderr="",
            )
        if operation == "get-object-retention":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "Retention": {
                            "Mode": retention_mode,
                            "RetainUntilDate": "2029-09-28T01:02:04Z",
                        }
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected operation {operation}")

    return runner, calls, temp_paths


def seed_journal(path: Path) -> None:
    JOURNAL.append_event(path, event())


def export(path: Path, runner, **overrides):
    values = {
        "journal_path": path,
        "bucket": "teracorp-backup",
        "prefix": "journal/offsite",
        "endpoint": "https://s3.us-west-004.backblazeb2.com",
        "retention_days": 1096,
        "access_key": "dedicated-access-key",
        "secret_key": "dedicated-secret-key",
        "runner": runner,
        "clock": fixed_clock,
        **overrides,
    }
    return EXPORTER.export_snapshot(**values)


@pytest.mark.parametrize("tamper", ["hash", "truncated", "unminimized"])
def test_invalid_or_tampered_journal_makes_zero_provider_calls(tmp_path, tamper) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    if tamper == "hash":
        record = json.loads(path.read_text())
        record["event"]["actor_id"] = "other-agent"
        path.write_text(json.dumps(record) + "\n")
        path.chmod(0o600)
    elif tamper == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    else:
        record = json.loads(path.read_text())
        record["event"]["private_text"] = "do not export"
        record["entry_hash"] = JOURNAL._hash(record["sequence"], record["event"], record["previous_hash"])
        path.write_bytes(JOURNAL._canonical(record) + b"\n")
        path.chmod(0o600)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="journal snapshot validation failed"):
        export(path, runner)
    assert calls == []


@pytest.mark.parametrize(
    ("bucket", "prefix", "endpoint", "days"),
    [
        ("bad_bucket", "journal/offsite", "https://s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "../unsafe", "https://s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "/absolute", "https://s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "journal//offsite", "https://s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "journal/offsite", "http://s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "journal/offsite", "https://evil.example", 1),
        ("teracorp-backup", "journal/offsite", "https://s3.us-west-004.backblazeb2.com/path", 1),
        ("teracorp-backup", "journal/offsite", "https://user@s3.us-west-004.backblazeb2.com", 1),
        ("teracorp-backup", "journal/offsite", "https://s3.us-west-004.backblazeb2.com?x=1", 1),
        ("teracorp-backup", "journal/offsite", "https://s3.us-west-004.backblazeb2.com", 0),
        ("teracorp-backup", "journal/offsite", "https://s3.us-west-004.backblazeb2.com", 3001),
        ("teracorp-backup", "journal/offsite", "https://s3.us-west-004.backblazeb2.com", True),
    ],
)
def test_invalid_destination_refuses_before_provider_calls(tmp_path, bucket, prefix, endpoint, days) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError):
        export(path, runner, bucket=bucket, prefix=prefix, endpoint=endpoint, retention_days=days)
    assert calls == []


def test_oversized_snapshot_refuses_before_provider_calls(tmp_path, monkeypatch) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    monkeypatch.setattr(EXPORTER, "MAX_SNAPSHOT_BYTES", 10)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="journal snapshot validation failed"):
        export(path, runner)
    assert calls == []


def test_verified_export_uses_fixed_argv_child_only_credentials_and_private_tempfiles(tmp_path, monkeypatch) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-must-not-pass")
    monkeypatch.setenv("AWS_PROFILE", "ambient-profile-must-not-pass")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://attacker.invalid")
    runner, calls, temp_paths = runner_for()
    result = export(path, runner)

    assert result["status"] == "verified"
    assert result["record_count"] == 1
    assert result["retain_until"] == "2029-09-28T01:02:04Z"
    assert len(calls) == 6
    assert [argv[argv.index("s3api") + 1] for argv, _ in calls] == [
        "put-object",
        "head-object",
        "get-object-retention",
        "put-object",
        "head-object",
        "get-object-retention",
    ]
    for argv, child_env in calls:
        assert argv[0] == "aws"
        assert "--endpoint-url" in argv
        assert "shell" not in child_env
        assert child_env["AWS_ACCESS_KEY_ID"] == "dedicated-access-key"
        assert child_env["AWS_SECRET_ACCESS_KEY"] == "dedicated-secret-key"
        assert child_env["AWS_CONFIG_FILE"] == "/dev/null"
        assert child_env["AWS_SHARED_CREDENTIALS_FILE"] == "/dev/null"
        assert child_env["AWS_EC2_METADATA_DISABLED"] == "true"
        assert "AWS_PROFILE" not in child_env
        assert "AWS_ENDPOINT_URL" not in child_env
        assert "ambient-must-not-pass" not in child_env.values()
        assert "ambient-profile-must-not-pass" not in child_env.values()
    assert all(not item.exists() for item in temp_paths)
    assert not temp_paths[0].parent.exists()


def test_uploaded_snapshot_is_exact_verified_chain_and_manifest_is_canonical_metadata(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    original = path.read_bytes()
    delegate, _, _ = runner_for()
    uploaded = []

    def runner(argv, env, timeout):
        if argv[argv.index("s3api") + 1] == "put-object":
            uploaded.append(Path(argv[argv.index("--body") + 1]).read_bytes())
        return delegate(argv, env, timeout)

    result = export(path, runner)
    manifest_bytes = uploaded[1]
    manifest = json.loads(manifest_bytes)
    assert uploaded[0] == original
    assert manifest_bytes == JOURNAL._canonical(manifest) + b"\n"
    assert manifest["snapshot_sha256"] == result["snapshot_sha256"]
    assert manifest["head_hash"] == result["head_hash"]
    assert manifest["snapshot_key"] == result["snapshot_key"]
    assert "evt-export" not in manifest_bytes.decode("utf-8")


def test_snapshot_waits_for_the_existing_journal_process_lock(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    lock_fd = os.open(f"{path}.lock", os.O_RDONLY)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    runner, calls, _ = runner_for()
    started = threading.Event()

    def invoke():
        started.set()
        return export(path, runner)

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(invoke)
    try:
        assert started.wait(timeout=2)
        assert not future.done()
        assert calls == []
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    try:
        result = future.result(timeout=5)
    finally:
        pool.shutdown(wait=True)
    assert result["status"] == "verified"
    assert len(calls) == 6


@pytest.mark.parametrize("operation", ["put-object", "head-object", "get-object-retention"])
def test_provider_failures_are_sanitized_and_fail_closed(tmp_path, operation, capsys) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, temp_paths = runner_for(fail_operation=operation)
    with pytest.raises(EXPORTER.ExportError) as error:
        export(path, runner)
    assert "PRIVATE_PROVIDER_ERROR" not in str(error.value)
    assert "secret-value" not in str(error.value)
    assert len(calls) <= 3
    assert all(not item.exists() for item in temp_paths)
    output = capsys.readouterr()
    assert "secret-value" not in output.out + output.err


def test_retention_mismatch_or_size_mismatch_is_refused(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, _, _ = runner_for(retention_mode="GOVERNANCE")
    with pytest.raises(EXPORTER.ExportError, match="retention"):
        export(path, runner)

    runner, calls, _ = runner_for()
    original = runner

    def wrong_size(argv, env, timeout):
        result = original(argv, env, timeout)
        if argv[argv.index("s3api") + 1] == "head-object":
            result.stdout = json.dumps({"ContentLength": 999999})
        return result

    with pytest.raises(EXPORTER.ExportError, match="size verification"):
        export(path, wrong_size)
    assert calls


def test_repeated_exports_use_distinct_opaque_run_keys(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    ids = iter(["1" * 32, "2" * 32])
    first_runner, _, _ = runner_for()
    second_runner, _, _ = runner_for()
    first = export(path, first_runner, run_id_factory=lambda: next(ids))
    second = export(path, second_runner, run_id_factory=lambda: next(ids))
    assert first["snapshot_key"] != second["snapshot_key"]
    assert first["manifest_key"] != second["manifest_key"]
    assert first["run_id"] == "1" * 32
    assert second["run_id"] == "2" * 32


def test_missing_dedicated_credentials_refuse_before_provider_calls(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="credentials"):
        export(path, runner, access_key="", secret_key="")
    assert calls == []


def test_upload_metadata_omission_refuses_without_followup_calls(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    calls = []

    def runner(argv, env, timeout):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": "2029-09-28T01:02:04Z"}),
            stderr="",
        )

    with pytest.raises(EXPORTER.ExportError, match="omitted object metadata"):
        export(path, runner)
    assert len(calls) == 1


def test_runner_exception_is_sanitized(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)

    def runner(argv, env, timeout):
        raise RuntimeError("credentials=secret-value provider leaked snapshot bytes")

    with pytest.raises(EXPORTER.ExportError, match="put-object operation failed") as error:
        export(path, runner)
    assert "secret-value" not in str(error.value)
    assert "snapshot bytes" not in str(error.value)


def test_cli_reads_only_dedicated_credential_environment_names(tmp_path, monkeypatch, capsys) -> None:
    seen = {}

    def fake_export(**kwargs):
        seen.update(kwargs)
        return {"status": "verified", "run_id": "a" * 32}

    monkeypatch.setenv(EXPORTER.ACCESS_KEY_ENV, "dedicated-cli-access")
    monkeypatch.setenv(EXPORTER.SECRET_KEY_ENV, "dedicated-cli-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-access-must-not-be-used")
    monkeypatch.setattr(EXPORTER, "export_snapshot", fake_export)
    result = EXPORTER.main(
        [
            str(tmp_path / "actions.jsonl"),
            "--bucket",
            "teracorp-backup",
            "--prefix",
            "journal/offsite",
            "--endpoint",
            "https://s3.us-west-004.backblazeb2.com",
            "--retention-days",
            "365",
        ]
    )
    assert result == 0
    assert seen["access_key"] == "dedicated-cli-access"
    assert seen["secret_key"] == "dedicated-cli-secret"
    output = capsys.readouterr()
    assert "dedicated-cli-access" not in output.out + output.err
    assert "dedicated-cli-secret" not in output.out + output.err
    assert "ambient-access-must-not-be-used" not in output.out + output.err


def test_default_subprocess_runner_uses_shell_false_and_suppresses_provider_output(monkeypatch) -> None:
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="private", stderr="private")

    monkeypatch.setattr(EXPORTER.subprocess, "run", fake_run)
    result = EXPORTER._default_runner(["aws", "s3api"], {"PATH": "/bin"}, 2.0)
    assert observed["shell"] is False
    assert observed["check"] is False
    assert observed["stdin"] == EXPORTER.subprocess.DEVNULL
    assert observed["capture_output"] is True
    assert result.stderr == "private"  # captured for the caller, never emitted by exporter


def test_exporter_does_not_read_snapshot_content_to_terminal(tmp_path, capsys) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, _, _ = runner_for()
    export(path, runner)
    captured = capsys.readouterr()
    assert "evt-export" not in captured.out + captured.err
    assert "payload_sha256" not in captured.out + captured.err
