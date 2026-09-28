from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/weekly_rhythm.py"
SPEC = importlib.util.spec_from_file_location("weekly_rhythm", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RHYTHM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RHYTHM)


def summaries() -> dict[str, dict]:
    return {
        "g2": {
            "schema_version": 1,
            "as_of_date": "2026-09-28",
            "products": [
                {
                    "product_id": "terakidz-kit",
                    "launch_date": "2026-01-01",
                    "currency": "IDR",
                    "checkpoints": [
                        {
                            "day": 60,
                            "due_date": "2026-03-02",
                            "observed_on": "2026-03-02",
                            "evidence_ref": "synthetic:60d",
                            "metrics": {
                                "revenue": {
                                    "status": "met",
                                    "observed": "1000",
                                    "direction": "gte",
                                    "unit": "idr",
                                    "threshold": "500",
                                }
                            },
                        },
                        {
                            "day": 90,
                            "due_date": "2026-04-01",
                            "observed_on": None,
                            "evidence_ref": None,
                            "metrics": {
                                "revenue": {
                                    "status": "unmeasured",
                                    "observed": None,
                                    "direction": "gte",
                                    "unit": "idr",
                                    "threshold": "500",
                                }
                            },
                        },
                    ],
                }
            ],
        },
        "g3": {
            "schema_version": 1,
            "trend_status": "four_consecutive_weeks",
            "four_week_total_minutes": 360,
            "four_week_change_minutes": 0,
            "weeks": [
                {
                    "week_start": f"2026-09-{day:02}",
                    "approval_minutes": 60,
                    "outage_response_minutes": 20,
                    "manual_fulfilment_minutes": 10,
                    "total_minutes": 90,
                    "change_vs_previous_week_minutes": None if day == 7 else 0,
                }
                for day in (7, 14, 21, 28)
            ],
        },
        "g4": {
            "schema_version": 1,
            "period": "2026-08",
            "currency": "IDR",
            "entities": [
                {
                    "entity_id": "terakidz",
                    "budget_amount": "100000",
                    "invoice_total": "25000",
                    "variance_amount": "75000",
                    "budget_position": "within_budget",
                }
            ],
            "invoice_count": 1,
            "unallocated_amount": "0",
            "total_budget": "100000",
            "total_invoice_amount": "25000",
            "total_variance": "75000",
        },
    }


def manifest(tmp_path: Path, *, as_of_date: str = "2026-09-28") -> dict:
    components = {}
    for name, summary in summaries().items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        limits = {"g2": 14, "g3": 10, "g4": 45}
        components[name] = {
            "path": path.name,
            "max_age_days": limits[name],
            "evidence_status": "unverified",
            "provenance_note": "synthetic_fixture",
        }
    return {"schema_version": 1, "as_of_date": as_of_date, "components": components}


def test_packet_includes_all_three_summaries_as_untrusted_and_manual_rhythm(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    result = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert [item["freshness_status"] for item in result["inputs"].values()] == ["fresh", "fresh", "fresh"]
    assert all(item["trust_status"] == "untrusted" for item in result["inputs"].values())
    assert result["inputs"]["g3"]["summary"]["four_week_total_minutes"] == 360
    assert (
        result["founder_rhythm"]["monday_priorities"] == "Founder writes priorities manually; no priority is inferred."
    )
    assert result["founder_rhythm"]["daily_approval_batch_minutes"] == {"minimum": 20, "maximum": 30}
    assert "decision" not in result and "recommendations" not in result
    assert result["g1_four_week_execution_status"] == "not_established"


def test_omitted_components_are_visibly_missing() -> None:
    result = RHYTHM.build_packet(
        {"schema_version": 1, "as_of_date": "2026-09-28", "components": {}}, base_dir=Path(".")
    )
    assert {name: value["freshness_status"] for name, value in result["inputs"].items()} == {
        "g2": "missing",
        "g3": "missing",
        "g4": "missing",
    }


@pytest.mark.parametrize(
    ("name", "summary_date", "expected"),
    [
        ("g2", "2026-08-01", "stale"),
        ("g2", "2026-09-29", "future"),
        ("g3", "2026-09-14", "stale"),
        ("g3", "2026-10-05", "future"),
    ],
)
def test_stale_and_future_inputs_are_distinguished(tmp_path: Path, name: str, summary_date: str, expected: str) -> None:
    data = manifest(tmp_path)
    path = tmp_path / data["components"][name]["path"]
    summary = json.loads(path.read_text(encoding="utf-8"))
    if name == "g2":
        summary["as_of_date"] = summary_date
    else:
        if expected == "stale":
            for week, day in zip(
                summary["weeks"], ("2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07"), strict=True
            ):
                week["week_start"] = day
        else:
            for week, day in zip(
                summary["weeks"], ("2026-09-14", "2026-09-21", "2026-09-28", summary_date), strict=True
            ):
                week["week_start"] = day
        summary["weeks"][0]["change_vs_previous_week_minutes"] = None
    path.write_text(json.dumps(summary), encoding="utf-8")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert result["inputs"][name]["freshness_status"] == expected


def test_g4_future_period_is_not_fresh(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    g4_path = tmp_path / data["components"]["g4"]["path"]
    summary = json.loads(g4_path.read_text(encoding="utf-8"))
    summary["period"] = "2026-10"
    g4_path.write_text(json.dumps(summary), encoding="utf-8")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert result["inputs"]["g4"]["freshness_status"] == "future"


def test_g4_old_period_is_stale(tmp_path: Path) -> None:
    data = manifest(tmp_path, as_of_date="2026-09-28")
    g4_path = tmp_path / data["components"]["g4"]["path"]
    summary = json.loads(g4_path.read_text(encoding="utf-8"))
    summary["period"] = "2026-07"
    g4_path.write_text(json.dumps(summary), encoding="utf-8")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert result["inputs"]["g4"]["freshness_status"] == "stale"


def test_g4_accepts_bounded_aggregates_larger_than_source_amount_limit(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    g4_path = tmp_path / data["components"]["g4"]["path"]
    total = str(2 * int("9" * 64))
    summary = {
        "schema_version": 1,
        "period": "2026-09",
        "currency": "IDR",
        "entities": [
            {
                "entity_id": "terakod",
                "budget_amount": "0",
                "invoice_total": total,
                "variance_amount": f"-{total}",
                "budget_position": "over_budget",
            }
        ],
        "invoice_count": 2,
        "unallocated_amount": "0",
        "total_budget": "0",
        "total_invoice_amount": total,
        "total_variance": f"-{total}",
    }
    g4_path.write_text(json.dumps(summary), encoding="utf-8")
    packet = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert packet["inputs"]["g4"]["summary"]["total_invoice_amount"] == total


def test_evidence_claim_never_makes_input_trusted(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    data["components"]["g2"]["evidence_status"] = "founder_attested"
    result = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert result["inputs"]["g2"]["trust_status"] == "untrusted"
    assert result["inputs"]["g2"]["caller_evidence_status"] == "founder_attested"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(extra=True),
        lambda data: data.update(as_of_date="2026-9-28"),
        lambda data: data["components"].update(g5={}),
        lambda data: data["components"]["g2"].update(max_age_days=1000),
        lambda data: data["components"]["g2"].update(evidence_status="verified"),
        lambda data: data["components"]["g2"].update(path="../secret.env"),
    ],
)
def test_invalid_manifest_refuses(tmp_path: Path, mutate) -> None:
    data = manifest(tmp_path)
    mutate(data)
    with pytest.raises(RHYTHM.InputError):
        RHYTHM.build_packet(data, base_dir=tmp_path)


def test_unknown_summary_fields_refuse_possible_unsanitized_content(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    path = tmp_path / data["components"]["g2"]["path"]
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["customer_name"] = "private"
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RHYTHM.InputError, match="keys invalid"):
        RHYTHM.build_packet(data, base_dir=tmp_path)


@pytest.mark.parametrize("evidence_ref", ["customer:jane.doe/order-123", "evidence:jane-doe"])
def test_evidence_reference_rejects_customer_names_and_nonopaque_labels(tmp_path: Path, evidence_ref: str) -> None:
    data = manifest(tmp_path)
    path = tmp_path / data["components"]["g2"]["path"]
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["products"][0]["checkpoints"][0]["evidence_ref"] = evidence_ref
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RHYTHM.InputError, match="opaque label"):
        RHYTHM.build_packet(data, base_dir=tmp_path)


def _set_g4_period(tmp_path: Path, data: dict, period: str) -> None:
    g4_path = tmp_path / data["components"]["g4"]["path"]
    summary = json.loads(g4_path.read_text(encoding="utf-8"))
    summary["period"] = period
    g4_path.write_text(json.dumps(summary), encoding="utf-8")


@pytest.mark.parametrize("as_of_date", ["2026-09-01", "2026-09-15", "2026-09-20", "2026-09-25", "2026-09-30"])
def test_g4_latest_closed_month_is_fresh_all_month(tmp_path: Path, as_of_date: str) -> None:
    data = manifest(tmp_path, as_of_date=as_of_date)
    _set_g4_period(tmp_path, data, "2026-08")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)["inputs"]["g4"]
    assert result["freshness_status"] == "fresh"
    # Age is measured from the day after the period closes, not from the month's first day.
    assert result["age_days"] == (RHYTHM.date.fromisoformat(as_of_date) - RHYTHM.date(2026, 9, 1)).days


def test_g4_age_is_anchored_at_period_end_for_older_closed_months(tmp_path: Path) -> None:
    data = manifest(tmp_path, as_of_date="2026-10-10")
    _set_g4_period(tmp_path, data, "2026-08")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)["inputs"]["g4"]
    assert result["age_days"] == 39
    assert result["freshness_status"] == "fresh"
    data = manifest(tmp_path, as_of_date="2026-10-20")
    _set_g4_period(tmp_path, data, "2026-08")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)["inputs"]["g4"]
    assert result["age_days"] == 49
    assert result["freshness_status"] == "stale"


def test_g4_unclosed_current_month_is_partial_not_fresh(tmp_path: Path) -> None:
    data = manifest(tmp_path, as_of_date="2026-09-28")
    _set_g4_period(tmp_path, data, "2026-09")
    result = RHYTHM.build_packet(data, base_dir=tmp_path)["inputs"]["g4"]
    assert result["freshness_status"] == "partial"
    assert result["age_days"] is None


def test_g4_december_period_rolls_into_next_year(tmp_path: Path) -> None:
    data = manifest(tmp_path, as_of_date="2027-01-25")
    _set_g4_period(tmp_path, data, "2026-12")
    for name in ("g2", "g3"):
        del data["components"][name]
    result = RHYTHM.build_packet(data, base_dir=tmp_path)["inputs"]["g4"]
    assert result["freshness_status"] == "fresh"
    assert result["age_days"] == 24


def test_provenance_note_is_a_fixed_nonfreeform_label(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    data["components"]["g2"]["provenance_note"] = "Jane Doe jane@example.com"
    with pytest.raises(RHYTHM.InputError, match="approved label"):
        RHYTHM.build_packet(data, base_dir=tmp_path)


@pytest.mark.parametrize("period", ["0000-01", "9999-12"])
def test_g4_invalid_calendar_boundary_refuses_cleanly(tmp_path: Path, period: str) -> None:
    data = manifest(tmp_path)
    path = tmp_path / data["components"]["g4"]["path"]
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["period"] = period
    path.write_text(json.dumps(summary), encoding="utf-8")
    if period == "9999-12":
        # This period is valid; the packet should classify it as future.
        packet = RHYTHM.build_packet(data, base_dir=tmp_path)
        assert packet["inputs"]["g4"]["freshness_status"] == "future"
    else:
        with pytest.raises(RHYTHM.InputError, match="valid calendar month"):
            RHYTHM.build_packet(data, base_dir=tmp_path)


def test_g2_checkpoint_date_overflow_refuses_as_input_error() -> None:
    summary = summaries()["g2"]
    summary["products"][0]["launch_date"] = "9999-12-31"
    with pytest.raises(RHYTHM.InputError, match="checkpoint range"):
        RHYTHM._validate_g2(summary)


def test_summary_symlink_is_refused(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    target = tmp_path / "target.json"
    target.write_text((tmp_path / "g2.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "g2.json").unlink()
    (tmp_path / "g2.json").symlink_to(target)
    with pytest.raises(RHYTHM.InputError, match="cannot read g2 summary"):
        RHYTHM.build_packet(data, base_dir=tmp_path)


def test_deterministic_packet_and_read_only_inputs(tmp_path: Path) -> None:
    data = manifest(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}
    first = RHYTHM.build_packet(data, base_dir=tmp_path)
    second = RHYTHM.build_packet(data, base_dir=tmp_path)
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert before == {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}


def test_cli_duplicate_keys_and_oversize_fail_cleanly(tmp_path: Path, capsys) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert RHYTHM.main([str(path)]) == 2
    assert "duplicate JSON object key" in capsys.readouterr().err
    path.write_bytes(b" " * (RHYTHM._MAX_INPUT_BYTES + 1))
    assert RHYTHM.main([str(path)]) == 2
    assert "must not exceed" in capsys.readouterr().err


def test_checked_in_synthetic_packet_runs_through_cli(capsys) -> None:
    example = SCRIPT.parents[1] / "metrics/ops1/examples/manifest.synthetic.json"
    assert RHYTHM.main([str(example)]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["g1_four_week_execution_status"] == "not_established"
    assert all(item["trust_status"] == "untrusted" for item in packet["inputs"].values())


def test_script_has_no_network_database_or_process_dependencies() -> None:
    import ast

    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})


def test_unknown_key_error_cannot_inject_terminal_controls() -> None:
    hostile_key = "\x1b[2J"
    with pytest.raises(RHYTHM.InputError) as error:
        RHYTHM._object({hostile_key: "value"}, "manifest", {"schema_version"})
    assert "\x1b" not in str(error.value)
