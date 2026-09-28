from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teracorp_g7_documents as documents  # noqa: E402
import teracorp_privacy_inventory as inventory  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/scripts/teracorp_g7_documents.py"
SAMPLE = ROOT / "ops/examples/teracorp_privacy_inventory.synthetic.v2.json"
RECORD_FILES = sorted((ROOT / "ops/examples").glob("teracorp_g7_deletion_evidence.synthetic-*.v1.json"))
AS_OF = "2026-09-28"


def payload() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def view(data: dict | None = None, **kwargs) -> inventory.InventoryView:
    records = [inventory.parse_record(inventory.load(path)) for path in RECORD_FILES]
    return inventory.inspect(data or payload(), AS_OF, records, allow_local_fake=True, **kwargs)


def test_four_documents_per_product_with_unverified_banner() -> None:
    rendered = documents.render(view(), "0" * 64)
    assert set(rendered) == {product["product_id"] for product in payload()["products"]}
    for docs in rendered.values():
        assert set(docs) == set(documents.DOCUMENTS)
        for text in docs.values():
            assert "UNVERIFIED — STRUCTURALLY COMPLETE" in text
            assert "compliant" not in text.lower()


def test_documents_reflect_the_inventory_content() -> None:
    data = payload()
    product = data["products"][0]
    docs = documents.render(view(data), "0" * 64)[product["product_id"]]
    for store in product["stores"]:
        assert store["store_id"] in docs["data-map.md"]
        assert store["processor_id"] in docs["processors.md"]
    for flow in product["data_flows"]:
        assert flow["flow_id"] in docs["data-map.md"]
    assert "payment_metadata | 3650 | transaction_date | anonymise" in docs["retention.md"]
    assert "digest tombstone + expiry" in docs["retention.md"]
    assert "High-sensitivity categories requiring counsel review: payment_metadata" in docs["pia-skeleton.md"]
    assert "UNASSESSED (founder/counsel)" in docs["pia-skeleton.md"]
    record_sha = inventory.parse_record(inventory.load(RECORD_FILES[0])).digest
    assert record_sha in docs["pia-skeleton.md"]
    assert record_sha in docs["data-map.md"]


def test_unresolved_items_are_listed_and_banner_says_incomplete() -> None:
    data = payload()
    data["products"][1]["retention_rules"] = []
    docs = documents.render(view(data), "0" * 64)[data["products"][1]["product_id"]]
    assert "UNVERIFIED — INCOMPLETE" in docs["retention.md"]
    assert "`products[1]:retention_rule:child_data:missing`" in docs["retention.md"]
    assert "| child_data | MISSING |" in docs["retention.md"]
    assert "High-sensitivity categories requiring counsel review: child_data" in docs["pia-skeleton.md"]


def test_missing_deletion_record_is_called_out() -> None:
    data = payload()
    rendered = documents.render(inventory.inspect(data, AS_OF), "0" * 64)
    docs = rendered[data["products"][0]["product_id"]]
    assert "Evidence record: **none matched**" in docs["data-map.md"]
    assert "deletion_record:missing" in docs["pia-skeleton.md"]


def test_cli_writes_documents_and_refuses_invalid_input(tmp_path: Path) -> None:
    args = [item for path in RECORD_FILES for item in ("--deletion-record", str(path))]
    out = tmp_path / "docs"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--as-of", AS_OF, "--allow-local-fake-evidence", *args, "--out", str(out)]
        + [str(SAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    summary = json.loads(result.stdout)
    assert summary["inventory_status"] == inventory.OK_STATUS
    assert len(summary["written"]) == 8
    assert len(list(out.glob("prod_*/*.md"))) == 8

    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 2}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--as-of", AS_OF, "--out", str(tmp_path / "none"), str(bad)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not (tmp_path / "none").exists()
