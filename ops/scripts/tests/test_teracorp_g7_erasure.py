from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teracorp_g7_erasure as erasure  # noqa: E402
import teracorp_privacy_inventory as inventory  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "ops/scripts/teracorp_g7_erasure.py"
VALIDATOR = ROOT / "ops/scripts/teracorp_privacy_inventory.py"
SAMPLE = ROOT / "ops/examples/teracorp_privacy_inventory.synthetic.v2.json"
AS_OF = "2026-09-28"
DAY = date.fromisoformat(AS_OF)


def payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def product(index: int = 0) -> dict:
    return payload()["products"][index]


def run(stores: list[dict], adapters=erasure.FAKE_ADAPTERS, **kwargs) -> dict:
    return erasure.run_deletion_test(
        product()["product_id"], stores, adapters, as_of=DAY, freshness_days=kwargs.pop("freshness_days", 30), **kwargs
    )


def outcomes(record: dict) -> dict[str, str]:
    return {item["store_kind"]: item["outcome"] for item in record["body"]["stores"]}


def bind(data: dict, index: int, record: dict) -> None:
    """Point the product's deletion evidence at a freshly emitted record, as an operator would."""
    ref = data["products"][index]["deletion_evidence_refs"][0]
    for item in data["evidence_refs"]:
        if item["evidence_ref_id"] == ref:
            item["sha256"] = inventory.record_body_digest(record["body"])
    status = record["body"]["status"]
    data["products"][index]["deletion_test_status"] = status


def cli(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *args], check=False, capture_output=True, text=True)


# --------------------------------------------------------------------------- success path


def test_every_fake_store_is_seeded_erased_and_verified_absent_or_tombstoned() -> None:
    record = run(product()["stores"])
    body = record["body"]
    assert body["status"] == "passed"
    assert outcomes(record) == {
        "odoo_orm": "absent",
        "chatwoot_contact": "absent",
        "supabase_row": "absent",
        "backup_snapshot": "tombstoned",
    }
    assert body["valid_until"] == (DAY + timedelta(days=30)).isoformat()
    assert body["store_map_sha256"] == inventory.store_map_digest(product()["stores"])
    assert inventory.parse_record(record).status == "passed"


def test_record_never_contains_synthetic_identifiers() -> None:
    subject = erasure.SyntheticSubject.generate()
    text = json.dumps(run(product()["stores"], subject=subject))
    for identifier in subject.identifiers:
        assert identifier not in text
    assert subject.digest in text


def test_emitted_records_satisfy_the_validator_end_to_end() -> None:
    data = payload()
    produced = erasure.run_inventory(data, AS_OF, erasure.FAKE_ADAPTERS, freshness_days=30)
    assert [record["body"]["status"] for record in produced] == ["passed", "passed"]
    for index, record in enumerate(produced):
        bind(data, index, record)
    parsed = [inventory.parse_record(record) for record in produced]
    report = inventory.validate(data, AS_OF, parsed, allow_local_fake=True)
    assert report["status"] == inventory.OK_STATUS, report["unresolved"]


# --------------------------------------------------------------------------- residue and missing adapters


class LeakyOdooAdapter(erasure.FakeOdooAdapter):
    """Erasure routine that forgets the order note: residue must be detected."""

    def erase(self, subject, as_of):
        for partner in self.env.search("res.partner", "email", subject.email):
            self.env.write("res.partner", [partner], {"name": "x", "email": False, "phone": False})


class LeakySupabaseAdapter(erasure.SqliteSupabaseAdapter):
    def erase(self, subject, as_of):
        self.db.execute("DELETE FROM auth_users WHERE email = ?", (subject.email,))
        self.db.commit()


class NoTombstoneBackupAdapter(erasure.FakeBackupAdapter):
    def erase(self, subject, as_of):
        return None


class OverlongBackupAdapter(erasure.FakeBackupAdapter):
    def __init__(self, spec):
        super().__init__(spec)
        self.snapshot_lifetime_days = (spec.retention_days or 0) + 400


class RawTombstoneBackupAdapter(erasure.FakeBackupAdapter):
    def erase(self, subject, as_of):
        self.tombstones.append({"subject_digest": subject.digest, "recorded_on": as_of, "email": subject.email})


class CrashingChatwootAdapter(erasure.FakeChatwootAdapter):
    def erase(self, subject, as_of):
        raise RuntimeError("upstream 500")


class EmptySeedAdapter(erasure.SqliteSupabaseAdapter):
    def seed(self, subject, as_of):
        return None


@pytest.mark.parametrize(
    ("kind", "adapter", "expected"),
    [
        ("odoo_orm", LeakyOdooAdapter, "residue"),
        ("supabase_row", LeakySupabaseAdapter, "residue"),
        ("backup_snapshot", NoTombstoneBackupAdapter, "tombstone_missing"),
        ("backup_snapshot", OverlongBackupAdapter, "retention_exceeded"),
        ("backup_snapshot", RawTombstoneBackupAdapter, "residue"),
        ("chatwoot_contact", CrashingChatwootAdapter, "erasure_error"),
        ("supabase_row", EmptySeedAdapter, "seed_not_observable"),
    ],
)
def test_a_store_that_fails_erasure_fails_the_product(kind, adapter, expected) -> None:
    adapters = dict(erasure.FAKE_ADAPTERS, **{kind: adapter})
    record = run(product()["stores"], adapters)
    assert record["body"]["status"] == "failed"
    assert outcomes(record)[kind] == expected


def test_residue_record_drives_the_validator_and_cli_non_zero(tmp_path: Path) -> None:
    data = payload()
    adapters = dict(erasure.FAKE_ADAPTERS, odoo_orm=LeakyOdooAdapter)
    record = run(data["products"][0]["stores"], adapters)
    good = erasure.run_deletion_test(
        data["products"][1]["product_id"],
        data["products"][1]["stores"],
        erasure.FAKE_ADAPTERS,
        as_of=DAY,
        freshness_days=30,
    )
    bind(data, 0, record)
    bind(data, 1, good)
    document = tmp_path / "inventory.json"
    document.write_text(json.dumps(data), encoding="utf-8")
    paths = []
    for name, item in (("a", record), ("b", good)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(item), encoding="utf-8")
        paths += ["--deletion-record", str(path)]
    result = cli(VALIDATOR, "--as-of", AS_OF, "--allow-local-fake-evidence", *paths, str(document))
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "products[0]:deletion_test_status:failed" in report["unresolved"]
    assert "products[0]:deletion_record:failed" in report["unresolved"]

    # An operator who marks the product "passed" anyway is contradicted by the record.
    data["products"][0]["deletion_test_status"] = "passed"
    document.write_text(json.dumps(data), encoding="utf-8")
    result = cli(VALIDATOR, "--as-of", AS_OF, "--allow-local-fake-evidence", *paths, str(document))
    assert result.returncode == 1
    assert "products[0]:deletion_record:contradicts_inventory_status" in json.loads(result.stdout)["unresolved"]


def test_data_map_store_without_an_adapter_fails() -> None:
    stores = product()["stores"]
    adapters = {kind: factory for kind, factory in erasure.FAKE_ADAPTERS.items() if kind != "chatwoot_contact"}
    record = run(stores, adapters)
    assert record["body"]["status"] == "failed"
    assert outcomes(record)["chatwoot_contact"] == "no_adapter"
    other = [dict(stores[0], store_kind="other")]
    assert outcomes(run(other))["other"] == "no_adapter"


def test_retention_bound_semantics_do_not_apply_to_live_stores() -> None:
    backup = next(store for store in product()["stores"] if store["store_kind"] == "backup_snapshot")
    as_live = dict(backup, erasure_mode="erase", retention_days=None)
    record = run([as_live])
    assert record["body"]["status"] == "failed"
    assert outcomes(record)["backup_snapshot"] == "residue"


# --------------------------------------------------------------------------- signing, bounds, CLI


def test_signed_records_verify_and_forgeries_do_not() -> None:
    key = b"s" * 32
    key_id = "key_" + "a" * 32
    data = payload()
    produced = erasure.run_inventory(data, AS_OF, erasure.FAKE_ADAPTERS, freshness_days=30, hmac_key=key, key_id=key_id)
    for index, record in enumerate(produced):
        bind(data, index, record)
    parsed = [inventory.parse_record(record) for record in produced]
    assert inventory.validate(data, AS_OF, parsed, allow_local_fake=True, hmac_key=key)["status"] == inventory.OK_STATUS
    wrong = inventory.validate(data, AS_OF, parsed, allow_local_fake=True, hmac_key=b"t" * 32)
    assert "products[0]:deletion_record:signature_invalid" in wrong["unresolved"]


@pytest.mark.parametrize(
    "kwargs",
    [{"freshness_days": 0}, {"freshness_days": 367}, {"environment": "prod"}, {"hmac_key": b"k" * 32}],
)
def test_harness_refuses_unbounded_or_inconsistent_parameters(kwargs) -> None:
    with pytest.raises(inventory.InputError):
        run(product()["stores"], **kwargs)
    with pytest.raises(inventory.InputError):
        run([])


def test_cli_writes_records_and_exits_zero_only_when_all_pass(tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    result = cli(HARNESS, "--as-of", AS_OF, "--freshness-days", "30", "--out", str(out), str(SAMPLE))
    summary = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout
    assert summary["status"] == "passed"
    assert len(list(out.glob("*.deletion-evidence.json"))) == 2
    for item in summary["records"]:
        assert inventory.parse_record(inventory.load(Path(item["path"]))).digest == item["record_sha256"]

    data = payload()
    data["products"][0]["stores"][0]["store_kind"] = "other"
    document = tmp_path / "other.json"
    document.write_text(json.dumps(data), encoding="utf-8")
    result = cli(HARNESS, "--as-of", AS_OF, "--freshness-days", "30", "--out", str(out), str(document))
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "failed"

    result = cli(HARNESS, "--as-of", AS_OF, "--freshness-days", "30", "--out", str(out), str(tmp_path / "nope.json"))
    assert result.returncode == 2
