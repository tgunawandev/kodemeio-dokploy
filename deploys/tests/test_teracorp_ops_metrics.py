from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/teracorp_ops_metrics.py"
SPEC = importlib.util.spec_from_file_location("teracorp_ops_metrics", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


def scorecard() -> dict:
    launch = date(2026, 1, 1)
    return {
        "schema_version": 1,
        "as_of_date": "2026-09-28",
        "products": [
            {
                "product_id": "terakidz-kit",
                "launch_date": launch.isoformat(),
                "currency": "IDR",
                "metrics": [
                    {"metric_id": "net_revenue", "direction": "gte", "unit": "idr", "threshold": "1000000"},
                    {"metric_id": "refund_rate", "direction": "lte", "unit": "fraction", "threshold": "0.05"},
                ],
                "checkpoints": [
                    {
                        "day": day,
                        "due_date": (launch + timedelta(days=day)).isoformat(),
                        "observed_on": None,
                        "observations": {"net_revenue": None, "refund_rate": None},
                        "evidence_ref": None,
                    }
                    for day in (60, 90)
                ],
            }
        ],
    }


def test_scorecard_reports_declared_thresholds_without_recommending_a_decision() -> None:
    data = scorecard()
    data["products"][0]["checkpoints"][0].update(
        observed_on="2026-03-02",
        observations={"net_revenue": "1200000", "refund_rate": "0.07"},
        evidence_ref="evidence:scorecard-60d",
    )
    result = METRICS.evaluate_scorecards(data)
    checkpoint = result["products"][0]["checkpoints"][0]
    assert checkpoint["due_date"] == "2026-03-02"
    assert checkpoint["metrics"] == {
        "net_revenue": {
            "status": "met",
            "observed": "1200000",
            "direction": "gte",
            "unit": "idr",
            "threshold": "1000000",
        },
        "refund_rate": {
            "status": "missed",
            "observed": "0.07",
            "direction": "lte",
            "unit": "fraction",
            "threshold": "0.05",
        },
    }
    assert "decision" not in checkpoint
    assert "decision" not in result["products"][0]


def test_missing_scorecard_observations_are_unmeasured_not_zero() -> None:
    result = METRICS.evaluate_scorecards(scorecard())
    checkpoint = result["products"][0]["checkpoints"][0]
    assert checkpoint["metrics"]["net_revenue"] == {
        "status": "unmeasured",
        "observed": None,
        "direction": "gte",
        "unit": "idr",
        "threshold": "1000000",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(extra=True),
        lambda data: data["products"][0].update(product_id=""),
        lambda data: data["products"][0]["metrics"][0].update(threshold="-1"),
        lambda data: data["products"][0]["checkpoints"][0].update(decision="kill"),
        lambda data: data["products"][0]["checkpoints"].pop(),
        lambda data: data["products"][0]["checkpoints"][0].update(observed_on="2026-09-29"),
        lambda data: data["products"][0]["metrics"][0].update(threshold="1" * 65),
        lambda data: data["products"][0]["metrics"][0].update(unit="IDR"),
    ],
    ids=[
        "unknown-root-key",
        "invalid-product-id",
        "negative-threshold",
        "decision-forbidden",
        "missing-checkpoint",
        "future-observation",
        "decimal-too-long",
        "unsafe-unit",
    ],
)
def test_invalid_scorecard_refuses(mutate) -> None:
    data = scorecard()
    mutate(data)
    with pytest.raises(METRICS.InputError):
        METRICS.evaluate_scorecards(data)


def hours(*starts: str) -> dict:
    return {
        "schema_version": 1,
        "weeks": [
            {
                "week_start": start,
                "approval_minutes": 60,
                "outage_response_minutes": 30,
                "manual_fulfilment_minutes": 90,
            }
            for start in starts
        ],
    }


def test_founder_hours_reports_four_week_trend() -> None:
    data = hours("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26")
    data["weeks"][1]["approval_minutes"] = 90
    result = METRICS.summarize_founder_hours(data)
    assert result["trend_status"] == "four_consecutive_weeks"
    assert result["weeks"][-1]["total_minutes"] == 180
    assert result["four_week_total_minutes"] == 750
    assert result["weeks"][1]["change_vs_previous_week_minutes"] == 30
    assert result["four_week_change_minutes"] == 0


def test_founder_hours_gap_never_becomes_a_zero_week_or_trend() -> None:
    result = METRICS.summarize_founder_hours(hours("2026-01-05", "2026-01-12", "2026-01-26", "2026-02-02"))
    assert result["trend_status"] == "insufficient_consecutive_weeks"
    assert result["four_week_total_minutes"] is None
    assert result["four_week_change_minutes"] is None
    assert len(result["weeks"]) == 4
    assert result["weeks"][2]["change_vs_previous_week_minutes"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["weeks"][0].update(approval_minutes=True),
        lambda data: data["weeks"][0].update(outage_response_minutes=-1),
        lambda data: data["weeks"][0].update(customer_name="private"),
        lambda data: data["weeks"].append(data["weeks"][0].copy()),
        lambda data: data["weeks"][0].update(week_start="2026-01-06"),
        lambda data: data["weeks"][0].update(
            approval_minutes=5000, outage_response_minutes=3000, manual_fulfilment_minutes=3000
        ),
    ],
    ids=[
        "boolean-minutes",
        "negative-minutes",
        "pii-field",
        "duplicate-week",
        "not-monday",
        "week-total-exceeds-hours",
    ],
)
def test_invalid_founder_hours_refuses(mutate) -> None:
    data = hours("2026-01-05")
    mutate(data)
    with pytest.raises(METRICS.InputError):
        METRICS.summarize_founder_hours(data)


def test_cli_rejects_duplicate_json_keys(tmp_path, capsys) -> None:
    path = tmp_path / "hours.json"
    path.write_text('{"schema_version":1,"schema_version":1,"weeks":[]}', encoding="utf-8")
    assert METRICS.main(["hours-trend", str(path)]) == 2
    assert "duplicate JSON key" in capsys.readouterr().err


def test_cli_rejects_oversized_input_before_json_parsing(tmp_path, capsys) -> None:
    path = tmp_path / "hours.json"
    path.write_bytes(b" " * (METRICS._MAX_INPUT_BYTES + 1))
    assert METRICS.main(["hours-trend", str(path)]) == 2
    assert "must not exceed" in capsys.readouterr().err


def test_cli_rejects_excessive_json_nesting_before_parsing(tmp_path, capsys) -> None:
    path = tmp_path / "deeply-nested.json"
    nesting = METRICS._MAX_JSON_DEPTH + 1
    path.write_text("[" * nesting + "0" + "]" * nesting, encoding="utf-8")
    assert METRICS.main(["hours-trend", str(path)]) == 2
    assert f"{METRICS._MAX_JSON_DEPTH}-level JSON nesting limit" in capsys.readouterr().err


def test_json_brackets_inside_strings_do_not_count_toward_nesting_limit() -> None:
    bracket_text = "[]{}" * (METRICS._MAX_JSON_DEPTH + 1)
    raw = json.dumps({"marker": bracket_text})
    METRICS._enforce_json_depth(raw)
    assert json.loads(raw)["marker"] == bracket_text


def test_cli_normalizes_parser_recursion_error_to_sanitized_input_error(tmp_path, capsys, monkeypatch) -> None:
    path = tmp_path / "hours.json"
    path.write_text("{}", encoding="utf-8")

    def raise_recursion_error(*args, **kwargs):
        raise RecursionError("untrusted parser detail")

    monkeypatch.setattr(METRICS.json, "loads", raise_recursion_error)
    assert METRICS.main(["hours-trend", str(path)]) == 2
    error = capsys.readouterr().err
    assert "input must be valid bounded UTF-8 JSON" in error
    assert "untrusted parser detail" not in error


def test_cli_reads_explicit_input_and_emits_json_without_writing_files(tmp_path, capsys) -> None:
    path = tmp_path / "hours.json"
    path.write_text(json.dumps(hours("2026-01-05")), encoding="utf-8")
    before = path.read_bytes()
    assert METRICS.main(["hours-trend", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["weeks"][0]["total_minutes"] == 180
    assert path.read_bytes() == before


def test_script_has_no_network_or_database_dependencies() -> None:
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})


def test_checked_in_examples_are_synthetic_and_valid() -> None:
    examples = SCRIPT.parents[1] / "metrics/examples"
    scorecards = json.loads((examples / "product-scorecards.synthetic.json").read_text())
    hours_log = json.loads((examples / "founder-hours.synthetic.json").read_text())
    assert METRICS.evaluate_scorecards(scorecards)["products"][0]["product_id"] == "synthetic-kit"
    assert METRICS.summarize_founder_hours(hours_log)["trend_status"] == "four_consecutive_weeks"
