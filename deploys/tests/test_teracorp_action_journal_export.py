from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
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
        "event_id": "evt-" + hashlib.sha256(event_id.encode()).hexdigest()[:32],
        "occurred_at": "2026-09-28T08:30:00Z",
        "tenant_id": "ten-" + "1" * 32,
        "actor_kind": "agent",
        "actor_id": "act-" + "2" * 32,
        "action": "workflow.requested",
        "work_order_ref": "odoo:work.order/" + "3" * 32,
        "target_ref": "factory:template/" + "4" * 32,
        "approval_ref": None,
        "payload_sha256": "a" * 64,
    }


def fixed_clock() -> datetime:
    return datetime(2026, 9, 28, 1, 2, 3, 456789, tzinfo=UTC)


def runner_for(*, fail_operation: str | None = None, retention_mode: str = "COMPLIANCE"):
    calls: list[tuple[list[str], dict[str, str]]] = []
    temp_paths: list[Path] = []
    remote_objects: dict[tuple[str, str], bytes] = {}
    put_envs: list[dict[str, str]] = []

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
            put_envs.append(dict(env))
            file_path = Path(argv[argv.index("--body") + 1])
            temp_paths.append(file_path)
            assert argv[argv.index("--if-none-match") + 1] == "*"
            remote_objects[(bucket, key)] = file_path.read_bytes()
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
        if operation == "get-object":
            assert env["AWS_ACCESS_KEY_ID"] == "dedicated-read-access-key"
            assert env["AWS_SECRET_ACCESS_KEY"] == "dedicated-read-secret-key"
            assert env != put_envs[-1]
            destination = Path(argv[-1])
            destination.write_bytes(remote_objects[(bucket, key)])
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
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
        "read_access_key": "dedicated-read-access-key",
        "read_secret_key": "dedicated-read-secret-key",
        "expected_head": None,
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
        record["event"]["actor_id"] = "act-" + "5" * 32
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
        export(path, runner, expected_head="a" * 64)
    assert calls == []


def test_rebuilt_chain_tamper_does_not_match_independent_expected_head(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    expected = JOURNAL.verify_journal(path)["head_hash"]
    record = json.loads(path.read_text())
    record["event"]["actor_id"] = "act-" + "5" * 32
    record["entry_hash"] = JOURNAL._hash(record["sequence"], record["event"], record["previous_hash"])
    path.write_bytes(JOURNAL._canonical(record) + b"\n")
    path.chmod(0o600)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="expected journal head"):
        export(path, runner, expected_head=expected)
    assert calls == []


@pytest.mark.parametrize("expected_head", [None, "x" * 64, "A" * 64])
def test_invalid_expected_head_refuses_before_provider_calls(tmp_path, expected_head) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="expected journal head"):
        export(path, runner, expected_head=expected_head)
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
        export(
            path,
            runner,
            expected_head="a" * 64,
            bucket=bucket,
            prefix=prefix,
            endpoint=endpoint,
            retention_days=days,
        )
    assert calls == []


def test_oversized_snapshot_refuses_before_provider_calls(tmp_path, monkeypatch) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    monkeypatch.setattr(EXPORTER, "MAX_SNAPSHOT_BYTES", 10)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="journal snapshot validation failed"):
        export(path, runner, expected_head="a" * 64)
    assert calls == []


def test_verified_export_uses_fixed_argv_child_only_credentials_and_private_tempfiles(tmp_path, monkeypatch) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-must-not-pass")
    monkeypatch.setenv("AWS_PROFILE", "ambient-profile-must-not-pass")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://attacker.invalid")
    runner, calls, temp_paths = runner_for()
    expected_head = JOURNAL.verify_journal(path)["head_hash"]
    result = export(path, runner, expected_head=expected_head)

    assert result["status"] == "verified"
    assert result["record_count"] == 1
    assert result["retain_until"] == "2029-09-28T01:02:04Z"
    assert len(calls) == 8
    assert [argv[argv.index("s3api") + 1] for argv, _ in calls] == [
        "put-object",
        "head-object",
        "get-object-retention",
        "get-object",
        "put-object",
        "head-object",
        "get-object-retention",
        "get-object",
    ]
    for argv, child_env in calls:
        assert argv[0] == "aws"
        assert "--endpoint-url" in argv
        assert "shell" not in child_env
        operation = argv[argv.index("s3api") + 1]
        read_only = operation == "get-object"
        assert child_env["AWS_ACCESS_KEY_ID"] == ("dedicated-read-access-key" if read_only else "dedicated-access-key")
        assert child_env["AWS_SECRET_ACCESS_KEY"] == (
            "dedicated-read-secret-key" if read_only else "dedicated-secret-key"
        )
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

    result = export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
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
    expected_head = JOURNAL.verify_journal(path)["head_hash"]
    lock_fd = os.open(f"{path}.lock", os.O_RDONLY)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    runner, calls, _ = runner_for()
    started = threading.Event()

    def invoke():
        started.set()
        return export(path, runner, expected_head=expected_head)

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
    assert len(calls) == 8


@pytest.mark.parametrize("operation", ["put-object", "head-object", "get-object-retention", "get-object"])
def test_provider_failures_are_sanitized_and_fail_closed(tmp_path, operation, capsys) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, temp_paths = runner_for(fail_operation=operation)
    with pytest.raises(EXPORTER.ExportError) as error:
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    assert "PRIVATE_PROVIDER_ERROR" not in str(error.value)
    assert "secret-value" not in str(error.value)
    assert len(calls) <= (4 if operation == "get-object" else 3)
    assert all(not item.exists() for item in temp_paths)
    output = capsys.readouterr()
    assert "secret-value" not in output.out + output.err


def test_retention_mismatch_or_size_mismatch_is_refused(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, _, _ = runner_for(retention_mode="GOVERNANCE")
    with pytest.raises(EXPORTER.ExportError, match="retention"):
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])

    runner, calls, _ = runner_for()
    original = runner

    def wrong_size(argv, env, timeout):
        result = original(argv, env, timeout)
        if argv[argv.index("s3api") + 1] == "head-object":
            result.stdout = json.dumps({"ContentLength": 999999})
        return result

    with pytest.raises(EXPORTER.ExportError, match="size verification"):
        export(path, wrong_size, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    assert calls


@pytest.mark.parametrize("corruption", ["short", "altered"])
def test_remote_readback_must_match_exact_size_and_manifest_sha256(tmp_path, corruption) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()

    def corrupting_runner(argv, env, timeout):
        result = runner(argv, env, timeout)
        if argv[argv.index("s3api") + 1] == "get-object":
            destination = Path(argv[-1])
            data = destination.read_bytes()
            destination.write_bytes(data[:-1] if corruption == "short" else b"x" + data[1:])
        return result

    with pytest.raises(EXPORTER.ExportError, match="readback verification"):
        export(path, corrupting_runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    assert [args[args.index("s3api") + 1] for args, _ in calls].count("get-object") == 1


def test_repeated_exports_use_distinct_opaque_run_keys(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    ids = iter(["1" * 32, "2" * 32])
    first_runner, _, _ = runner_for()
    second_runner, _, _ = runner_for()
    expected_head = JOURNAL.verify_journal(path)["head_hash"]
    first = export(path, first_runner, expected_head=expected_head, run_id_factory=lambda: next(ids))
    second = export(path, second_runner, expected_head=expected_head, run_id_factory=lambda: next(ids))
    assert first["snapshot_key"] != second["snapshot_key"]
    assert first["manifest_key"] != second["manifest_key"]
    assert first["run_id"] == "1" * 32
    assert second["run_id"] == "2" * 32


def test_missing_dedicated_credentials_refuse_before_provider_calls(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="credentials"):
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"], access_key="", secret_key="")
    assert calls == []


def test_missing_read_only_credentials_refuse_before_provider_calls(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="credentials"):
        export(
            path,
            runner,
            expected_head=JOURNAL.verify_journal(path)["head_hash"],
            read_access_key="",
            read_secret_key="",
        )
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
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    assert len(calls) == 1


def test_runner_exception_is_sanitized(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)

    def runner(argv, env, timeout):
        raise RuntimeError("credentials=secret-value provider leaked snapshot bytes")

    with pytest.raises(EXPORTER.ExportError, match="put-object operation failed") as error:
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    assert "secret-value" not in str(error.value)
    assert "snapshot bytes" not in str(error.value)


def test_cli_reads_only_dedicated_credential_environment_names(tmp_path, monkeypatch, capsys) -> None:
    seen = {}

    def fake_export(**kwargs):
        seen.update(kwargs)
        return {"status": "verified", "run_id": "a" * 32}

    monkeypatch.setenv(EXPORTER.ACCESS_KEY_ENV, "dedicated-cli-access")
    monkeypatch.setenv(EXPORTER.SECRET_KEY_ENV, "dedicated-cli-secret")
    monkeypatch.setenv(EXPORTER.READ_ACCESS_KEY_ENV, "dedicated-cli-read-access")
    monkeypatch.setenv(EXPORTER.READ_SECRET_KEY_ENV, "dedicated-cli-read-secret")
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
            "--expected-head",
            "a" * 64,
        ]
    )
    assert result == 0
    assert seen["access_key"] == "dedicated-cli-access"
    assert seen["secret_key"] == "dedicated-cli-secret"
    assert seen["read_access_key"] == "dedicated-cli-read-access"
    assert seen["read_secret_key"] == "dedicated-cli-read-secret"
    assert seen["expected_head"] == "a" * 64
    output = capsys.readouterr()
    assert "dedicated-cli-access" not in output.out + output.err
    assert "dedicated-cli-secret" not in output.out + output.err
    assert "ambient-access-must-not-be-used" not in output.out + output.err


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_default_subprocess_runner_bounds_output_during_reading(stream) -> None:
    source = f"import sys; sys.{stream}.write('x' * {EXPORTER.MAX_PROVIDER_OUTPUT_BYTES + 1}); sys.{stream}.flush()"
    with pytest.raises(RuntimeError, match="output limit"):
        EXPORTER._default_runner([sys.executable, "-c", source], {"PATH": "/bin"}, 5.0)


def test_default_subprocess_runner_terminates_timed_out_process() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        EXPORTER._default_runner([sys.executable, "-c", "import time; time.sleep(5)"], {"PATH": "/bin"}, 0.05)


def test_default_subprocess_runner_uses_bounded_shell_free_capture() -> None:
    result = EXPORTER._default_runner(
        [sys.executable, "-c", "import sys; sys.stdout.write('private')"], {"PATH": "/bin"}, 2.0
    )
    assert result.returncode == 0
    assert result.stdout == "private"
    assert result.stderr == ""


def test_default_subprocess_runner_enforces_readback_file_size_in_child(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(EXPORTER, "MAX_SNAPSHOT_BYTES", 64)
    destination = tmp_path / "readback.bin"
    source = (
        "import signal, sys; "
        "signal.signal(signal.SIGXFSZ, signal.SIG_IGN); "
        "f = open(sys.argv[-1], 'wb'); f.write(b'x' * 1024); f.flush()"
    )
    result = EXPORTER._default_runner(
        [sys.executable, "-c", source, "s3api", "get-object", str(destination)], {"PATH": "/bin"}, 5.0
    )
    assert result.returncode != 0
    assert destination.stat().st_size <= 64


def test_exporter_does_not_read_snapshot_content_to_terminal(tmp_path, capsys) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    runner, _, _ = runner_for()
    export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    captured = capsys.readouterr()
    assert "evt-export" not in captured.out + captured.err
    assert "payload_sha256" not in captured.out + captured.err


# --- Previous-manifest anchor (nightly chain continuity) -------------------------------------------


def export_with_manifest(path: Path, tmp_path: Path, name: str, **overrides) -> tuple[dict, dict]:
    runner, calls, _ = runner_for()
    manifest_out = tmp_path / f"{name}.manifest.json"
    result = export(path, runner, manifest_out=manifest_out, **overrides)
    assert calls, "a verified export must reach the provider"
    return result, json.loads(manifest_out.read_bytes())


def rewrite_record(path: Path, sequence: int) -> None:
    """Rebuild a fully valid chain in which record ``sequence`` has different content."""
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    previous = JOURNAL._GENESIS
    for record in records:
        if record["sequence"] == sequence:
            record["event"]["actor_id"] = "act-" + "5" * 32
        record["previous_hash"] = previous
        record["entry_hash"] = JOURNAL._hash(record["sequence"], record["event"], previous)
        previous = record["entry_hash"]
    path.write_bytes(b"".join(JOURNAL._canonical(record) + b"\n" for record in records))
    path.chmod(0o600)
    JOURNAL.verify_journal(path)  # the rebuilt chain is internally valid


def test_previous_manifest_anchor_allows_appends_after_it(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    first, first_manifest = export_with_manifest(
        path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"]
    )
    assert first_manifest["anchor"] is None
    JOURNAL.append_event(path, event("evt-second"))
    JOURNAL.append_event(path, event("evt-third"))

    second, second_manifest = export_with_manifest(path, tmp_path, "second", anchor_manifest=first_manifest)

    assert second["status"] == "verified"
    assert second["record_count"] == 3
    assert second["head_hash"] == JOURNAL.verify_journal(path)["head_hash"]
    assert second_manifest["anchor"] == {"record_count": 1, "head_hash": first["head_hash"]}
    # The new manifest is the next anchor: a third run with no appends still continues the chain.
    third, _ = export_with_manifest(path, tmp_path, "third", anchor_manifest=second_manifest)
    assert third["head_hash"] == second["head_hash"]


@pytest.mark.parametrize("rewritten_sequence", [1, 2])
def test_rewriting_a_record_at_or_before_the_anchor_is_refused(tmp_path, rewritten_sequence) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    JOURNAL.append_event(path, event("evt-second"))
    _, anchor = export_with_manifest(path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"])
    rewrite_record(path, rewritten_sequence)
    JOURNAL.append_event(path, event("evt-third"))
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="does not continue from the previous export anchor"):
        export(path, runner, anchor_manifest=anchor)
    assert calls == []


def test_rewriting_only_records_after_the_anchor_still_continues_the_chain(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    _, anchor = export_with_manifest(path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"])
    JOURNAL.append_event(path, event("evt-second"))
    rewrite_record(path, 2)
    result, _ = export_with_manifest(path, tmp_path, "second", anchor_manifest=anchor)
    assert result["record_count"] == 2


def test_truncation_below_the_anchor_is_refused(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    JOURNAL.append_event(path, event("evt-second"))
    _, anchor = export_with_manifest(path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"])
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    path.chmod(0o600)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="does not continue from the previous export anchor"):
        export(path, runner, anchor_manifest=anchor)
    assert calls == []


def test_anchor_and_expected_head_are_both_enforced_when_supplied(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    _, anchor = export_with_manifest(path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"])
    JOURNAL.append_event(path, event("evt-second"))
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="expected journal head"):
        export(path, runner, anchor_manifest=anchor, expected_head="b" * 64)
    assert calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(bucket="other-teracorp-bucket"),
        lambda m: m.update(prefix="journal/elsewhere"),
        lambda m: m.update(record_count=True),
        lambda m: m.update(record_count=-1),
        lambda m: m.update(head_hash="A" * 64),
        lambda m: m.update(schema_version=2),
        lambda m: m.update(unexpected="value"),
        lambda m: m.pop("head_hash"),
        lambda m: m.update(object_lock_mode="GOVERNANCE"),
    ],
    ids=["bucket", "prefix", "bool-count", "negative-count", "upper-hash", "schema", "unknown", "missing", "mode"],
)
def test_malformed_or_foreign_anchor_manifest_refuses_before_provider_calls(tmp_path, mutate) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    _, anchor = export_with_manifest(path, tmp_path, "first", expected_head=JOURNAL.verify_journal(path)["head_hash"])
    mutate(anchor)
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="anchor manifest"):
        export(path, runner, anchor_manifest=anchor)
    assert calls == []


def test_manifest_out_matches_uploaded_manifest_and_is_private(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    delegate, _, _ = runner_for()
    uploaded = []

    def runner(argv, env, timeout):
        if argv[argv.index("s3api") + 1] == "put-object":
            uploaded.append(Path(argv[argv.index("--body") + 1]).read_bytes())
        return delegate(argv, env, timeout)

    manifest_out = tmp_path / "manifest.json"
    export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"], manifest_out=manifest_out)
    assert manifest_out.read_bytes() == uploaded[1]
    assert os.stat(manifest_out).st_mode & 0o777 == 0o600


def test_existing_manifest_out_refuses_before_provider_calls(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    manifest_out = tmp_path / "manifest.json"
    manifest_out.write_text("{}")
    runner, calls, _ = runner_for()
    with pytest.raises(EXPORTER.ExportError, match="manifest output"):
        export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"], manifest_out=manifest_out)
    assert calls == []
    assert manifest_out.read_text() == "{}"


def test_anchor_manifest_file_loader_is_bounded_and_strict(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    manifest_out = tmp_path / "first.manifest.json"
    runner, _, _ = runner_for()
    export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"], manifest_out=manifest_out)
    assert EXPORTER.load_anchor_manifest(manifest_out)["record_count"] == 1
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"record_count":1,"record_count":2}')
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (EXPORTER.MAX_ANCHOR_MANIFEST_BYTES + 1))
    link = tmp_path / "link.json"
    link.symlink_to(manifest_out)
    for bad in (duplicate, oversized, link, tmp_path / "missing.json"):
        with pytest.raises(EXPORTER.ExportError, match="anchor manifest"):
            EXPORTER.load_anchor_manifest(bad)


def test_cli_passes_anchor_manifest_and_manifest_out_without_expected_head(tmp_path, monkeypatch) -> None:
    seen = {}
    anchor_path = tmp_path / "previous.manifest.json"
    anchor_path.write_text('{"schema_version":1}')
    monkeypatch.setattr(EXPORTER, "load_anchor_manifest", lambda path: {"loaded_from": str(path)})

    def fake_export(**kwargs):
        seen.update(kwargs)
        return {"status": "verified", "run_id": "a" * 32}

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
            "--anchor-manifest",
            str(anchor_path),
            "--manifest-out",
            str(tmp_path / "next.manifest.json"),
        ]
    )
    assert result == 0
    assert seen["expected_head"] is None
    assert seen["anchor_manifest"] == {"loaded_from": str(anchor_path)}
    assert seen["manifest_out"] == tmp_path / "next.manifest.json"


# --- Transfer timeouts scale with object size ------------------------------------------------------


def test_transfer_timeout_scales_with_object_size_and_is_capped() -> None:
    assert EXPORTER._transfer_timeout(0) == 60.0
    assert EXPORTER._transfer_timeout(1024) == 60.0
    assert EXPORTER._transfer_timeout(EXPORTER.MAX_SNAPSHOT_BYTES) > 60.0 + 256
    assert EXPORTER._transfer_timeout(10 * EXPORTER.MAX_SNAPSHOT_BYTES) == EXPORTER.MAX_TRANSFER_TIMEOUT_SECONDS
    sizes = [0, 1 << 20, 64 << 20, 256 << 20]
    timeouts = [EXPORTER._transfer_timeout(size) for size in sizes]
    assert timeouts == sorted(timeouts)


def test_put_and_get_object_use_size_scaled_timeout(tmp_path, monkeypatch) -> None:
    path = tmp_path / "actions.jsonl"
    seed_journal(path)
    delegate, _, _ = runner_for()
    seen: list[tuple[str, float]] = []
    monkeypatch.setattr(EXPORTER, "_transfer_timeout", lambda size: 60.0 + 1000.0 + size)

    def runner(argv, env, timeout):
        operation = argv[argv.index("s3api") + 1]
        seen.append((operation, timeout))
        return delegate(argv, env, 60.0)

    export(path, runner, expected_head=JOURNAL.verify_journal(path)["head_hash"])
    for operation, timeout in seen:
        if operation in {"put-object", "get-object"}:
            assert timeout > 1060.0
        else:
            assert timeout == 60.0
