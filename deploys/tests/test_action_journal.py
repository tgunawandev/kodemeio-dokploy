from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/action_journal.py"
SPEC = importlib.util.spec_from_file_location("action_journal", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
JOURNAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JOURNAL)


def event(event_id: str = "evt-001") -> dict:
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


def _append_in_process(path: str, number: int) -> int:
    return JOURNAL.append_event(Path(path), event(f"proc-{number:03}"))["sequence"]


def test_append_builds_verifiable_chain_and_external_head_anchor(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    first = JOURNAL.append_event(path, event())
    second = JOURNAL.append_event(path, event("evt-002"))
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert second["previous_hash"] == first["entry_hash"]
    verified = JOURNAL.verify_journal(path, expected_head=second["entry_hash"])
    assert verified == {"record_count": 2, "head_hash": second["entry_hash"]}
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_tampered_record_refuses_verification(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    JOURNAL.append_event(path, event())
    record = json.loads(path.read_text(encoding="utf-8"))
    record["event"]["actor_id"] = "act-" + "5" * 32
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(JOURNAL.InputError, match="hash mismatch"):
        JOURNAL.verify_journal(path)


def test_external_anchor_detects_rebuilt_or_truncated_history(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    record = JOURNAL.append_event(path, event())
    with pytest.raises(JOURNAL.InputError, match="external expected head"):
        JOURNAL.verify_journal(path, expected_head="0" * 64)
    assert JOURNAL.verify_journal(path, expected_head=record["entry_hash"])["record_count"] == 1


def test_duplicate_event_id_refuses_without_changing_journal(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    JOURNAL.append_event(path, event())
    before = path.read_bytes()
    with pytest.raises(JOURNAL.InputError, match="event_id values must be unique"):
        JOURNAL.append_event(path, event())
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(description="private customer details"),
        lambda value: value.update(actor_kind="root"),
        lambda value: value.update(actor_kind=[]),
        lambda value: value.update(occurred_at="2026-09-28T08:30:00+07:00"),
        lambda value: value.update(payload_sha256="not-a-hash"),
        lambda value: value.update(approval_ref="https://example.invalid/approval"),
        lambda value: value.update(tenant_id="customer-jane-doe"),
        lambda value: value.update(actor_id="jane-doe"),
        lambda value: value.update(work_order_ref="customer:jane-doe/order-123"),
        lambda value: value.update(target_ref="factory:template/customer-jane-doe"),
    ],
    ids=[
        "free-text-refused",
        "actor-kind-refused",
        "actor-kind-wrong-type",
        "non-utc-timestamp-refused",
        "invalid-digest",
        "url-refused",
        "pii-like-tenant",
        "pii-like-actor",
        "pii-like-work-order-ref",
        "pii-like-target-ref",
    ],
)
def test_invalid_event_refuses(mutate, tmp_path) -> None:
    data = event()
    mutate(data)
    with pytest.raises(JOURNAL.InputError):
        JOURNAL.validate_event(data)
    with pytest.raises(JOURNAL.InputError):
        JOURNAL.append_event(tmp_path / "actions.jsonl", data)


def test_truncated_line_and_symlink_refuse(tmp_path) -> None:
    path = tmp_path / "partial.jsonl"
    path.write_bytes(b'{"sequence":1}')
    path.chmod(0o600)
    with pytest.raises(JOURNAL.InputError, match="newline terminated"):
        JOURNAL.append_event(path, event())

    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(JOURNAL.InputError):
        JOURNAL.append_event(link, event())

    fifo = tmp_path / "journal-pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(JOURNAL.InputError, match="regular files"):
        JOURNAL.append_event(fifo, event())


def test_cli_appends_and_verifies_explicit_files_without_reading_free_text(tmp_path, capsys) -> None:
    input_path = tmp_path / "event.json"
    journal_path = tmp_path / "actions.jsonl"
    input_path.write_text(json.dumps(event()), encoding="utf-8")
    before = input_path.read_bytes()
    assert JOURNAL.main(["append", str(journal_path), str(input_path)]) == 0
    append_output = json.loads(capsys.readouterr().out)
    assert append_output["sequence"] == 1
    assert input_path.read_bytes() == before
    assert JOURNAL.main(["verify", str(journal_path), "--expected-head", append_output["entry_hash"]]) == 0
    assert json.loads(capsys.readouterr().out)["record_count"] == 1


def test_concurrent_appends_are_serialized_and_leave_a_valid_chain(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda number: JOURNAL.append_event(path, event(f"evt-{number:03}")), range(1, 17)))
    assert sorted(record["sequence"] for record in records) == list(range(1, 17))
    assert JOURNAL.verify_journal(path)["record_count"] == 16


def test_cross_process_appends_are_serialized_and_leave_a_valid_chain(tmp_path) -> None:
    path = str(tmp_path / "process-actions.jsonl")
    with ProcessPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(_append_in_process, [path] * 12, range(1, 13)))
    assert sorted(sequences) == list(range(1, 13))
    assert JOURNAL.verify_journal(Path(path))["record_count"] == 12


def test_script_has_no_network_or_database_dependencies() -> None:
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})


def test_checked_in_journal_example_is_synthetic_and_valid() -> None:
    example = SCRIPT.parents[1] / "journal/examples/action.synthetic.json"
    payload = json.loads(example.read_text(encoding="utf-8"))
    assert JOURNAL.validate_event(payload)["event_id"] == "evt-" + "a" * 32
