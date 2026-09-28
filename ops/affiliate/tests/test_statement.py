from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from affiliate import InputError, reconcile  # noqa: E402
from redirect import RedirectConfig, Request, ShortLinkHandler  # noqa: E402
from statement import reconcile_statement  # noqa: E402

SAMPLE = ROOT / "examples" / "affiliate.synthetic.v1.json"
STATEMENT = ROOT / "examples" / "affiliate-statement.synthetic.v1.json"


def document() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def statement() -> dict:
    return json.loads(STATEMENT.read_text(encoding="utf-8"))


def commission(doc: dict, index: int, amount: int, *, day: int = 22) -> dict:
    click = copy.deepcopy(doc["events"][0])
    click["event_id"] = f"synthetic-click-{index:03d}"
    click["occurred_at"] = f"2026-09-{day:02d}T10:00:00Z"
    doc["events"].append(click)
    event = {
        "event_id": f"synthetic-commission-{index:03d}",
        "event_type": "commission",
        "synthetic": True,
        "occurred_at": f"2026-09-{day:02d}T11:00:00Z",
        "click_event_id": click["event_id"],
        "order_ref": f"synthetic-order-{index:03d}",
        "order_amount_minor": amount,
        "commission_minor": (amount * 500 + 5000) // 10000,
        "currency": "IDR",
    }
    doc["events"].append(event)
    return event


def test_matching_statement_reconciles() -> None:
    report = reconcile_statement(document(), statement())
    assert report["status"] == "reconciled"
    assert report["matched"] == [{"order_ref": "synthetic-order-001", "commission_minor": 500}]
    assert report["missing_from_statement"] == []
    assert report["extra_in_statement"] == []
    assert report["amount_mismatch"] == []
    assert report["computed_total_minor"] == report["statement_total_minor"] == 500
    assert report["difference_minor"] == 0
    assert report["payments_created"] is False
    assert report["statement_authenticated"] is False
    assert report["manual_review_required"] is True


def test_reports_matched_missing_extra_and_amount_mismatch_rows() -> None:
    doc = document()
    commission(doc, 2, 20000)  # tracked and reported with a different amount
    commission(doc, 3, 30000)  # tracked, advertiser omitted it
    stmt = statement()
    stmt["lines"] += [
        {"order_ref": "synthetic-order-002", "commission_minor": 900},
        {"order_ref": "synthetic-order-999", "commission_minor": 700},  # advertiser-only
    ]
    report = reconcile_statement(doc, stmt)
    assert report["status"] == "discrepancies"
    assert [row["order_ref"] for row in report["matched"]] == ["synthetic-order-001"]
    assert report["missing_from_statement"] == [{"order_ref": "synthetic-order-003", "commission_minor": 1500}]
    assert report["extra_in_statement"] == [{"order_ref": "synthetic-order-999", "commission_minor": 700}]
    assert report["amount_mismatch"] == [
        {"order_ref": "synthetic-order-002", "computed_minor": 1000, "statement_minor": 900, "difference_minor": 100}
    ]
    assert report["computed_total_minor"] == 500 + 1000 + 1500
    assert report["statement_total_minor"] == 500 + 900 + 700
    assert report["difference_minor"] == 3000 - 2100


def test_retried_postback_does_not_create_a_false_mismatch_against_the_statement() -> None:
    doc = document()
    retry = copy.deepcopy(doc["events"][1])
    retry["event_id"] = "synthetic-commission-retry"
    doc["events"].append(retry)
    report = reconcile_statement(doc, statement())
    assert report["status"] == "reconciled"
    assert report["computed_total_minor"] == 500


def test_commissions_outside_the_statement_period_are_excluded() -> None:
    doc = document()
    commission(doc, 4, 40000, day=27)
    stmt = statement()
    stmt["period_end"] = "2026-09-25"
    report = reconcile_statement(doc, stmt)
    assert report["status"] == "reconciled"
    assert report["out_of_period_count"] == 1


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda s: s.update(advertiser_id="other-advertiser"), "statement_advertiser_mismatch"),
        (lambda s: s.update(currency="USD"), "statement_currency_mismatch"),
        (lambda s: s.update(period_start="2026-10-01"), "statement_period_invalid"),
        (lambda s: s["lines"].append(copy.deepcopy(s["lines"][0])), "statement_duplicate_order_ref"),
        (lambda s: s.update(synthetic=False), "statement_invalid"),
        (lambda s: s["lines"][0].update(customer_email="x@example.test"), "statement_invalid"),
    ],
)
def test_invalid_statement_fails_closed(mutation, code) -> None:
    stmt = statement()
    mutation(stmt)
    with pytest.raises(InputError, match=f"^{code}$"):
        reconcile_statement(document(), stmt)


def test_blocked_tracking_document_cannot_be_reconciled() -> None:
    doc = document()
    doc["advertiser"]["approval"]["status"] = "pending"
    with pytest.raises(InputError):
        reconcile_statement(doc, statement())


def test_cli_exit_codes(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "statement.py"
    ok = subprocess.run(
        [sys.executable, str(script), str(SAMPLE), str(STATEMENT)], capture_output=True, text=True, check=False
    )
    assert ok.returncode == 0, ok.stdout
    assert json.loads(ok.stdout)["status"] == "reconciled"
    stmt = statement()
    stmt["lines"][0]["commission_minor"] = 499
    changed = tmp_path / "statement.json"
    changed.write_text(json.dumps(stmt), encoding="utf-8")
    bad = subprocess.run(
        [sys.executable, str(script), str(SAMPLE), str(changed)], capture_output=True, text=True, check=False
    )
    assert bad.returncode == 3
    assert json.loads(bad.stdout)["status"] == "discrepancies"
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    refused = subprocess.run(
        [sys.executable, str(script), str(SAMPLE), str(broken)], capture_output=True, text=True, check=False
    )
    assert refused.returncode == 1
    assert "Traceback" not in refused.stderr


def test_end_to_end_click_to_reconciled_commission(tmp_path: Path) -> None:
    """Short link -> logged click -> fake advertiser postback -> tracked total -> statement match."""
    doc = document()
    doc["events"] = []
    doc["links"][0]["click_id_param"] = "subid"
    clicks: list[dict] = []
    ids = iter(["click-e2e-001", "click-e2e-002"])
    handler = ShortLinkHandler(
        doc,
        RedirectConfig(salt=b"k" * 32),
        sink=clicks.append,
        clock=lambda: datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
        id_factory=lambda: next(ids),
    )
    first = handler(Request("GET", "/a/synthetic-link-001", {"User-Agent": "Mozilla/5.0 Mobile"}, "192.0.2.10"))
    second = handler(Request("GET", "/a/synthetic-link-001", {"User-Agent": "Mozilla/5.0"}, "192.0.2.11"))
    assert first.headers["Location"].endswith("?subid=click-e2e-001")
    assert second.headers["Location"].endswith("?subid=click-e2e-002")

    # The fake advertiser echoes the sub-id back in its conversion postback (sent twice: a retry).
    postback = {
        "event_type": "commission",
        "synthetic": True,
        "occurred_at": "2026-09-23T09:00:00Z",
        "click_event_id": first.headers["Location"].rsplit("=", 1)[1],
        "order_ref": "synthetic-order-e2e-1",
        "order_amount_minor": 250000,
        "commission_minor": 12500,
        "currency": "IDR",
    }
    doc["events"] = [
        *clicks,
        {**postback, "event_id": "postback-001"},
        {**postback, "event_id": "postback-001-retry"},
    ]
    tracked = reconcile(doc)
    assert tracked["click_count"] == 2
    assert tracked["commission_count"] == 1
    assert tracked["commission_total_minor"] == 12500

    stmt = statement()
    stmt["lines"] = [{"order_ref": "synthetic-order-e2e-1", "commission_minor": 12500}]
    report = reconcile_statement(doc, stmt)
    assert report["status"] == "reconciled"
    assert report["matched"] == [{"order_ref": "synthetic-order-e2e-1", "commission_minor": 12500}]
