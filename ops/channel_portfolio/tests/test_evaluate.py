"""Channel portfolio evaluator (Track B, slice TB5; spec B5).

Synthetic F0f-shaped aggregates -> the G2 scorecard evaluator (imported, never copied) ->
continue|pivot|stop per channel, plus a PROPOSAL of pending F10 items to cancel for stopped
channels. Offline, read-only, deterministic, PII-free.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
SPEC = importlib.util.spec_from_file_location("channel_portfolio_evaluate", ROOT / "evaluate.py")
assert SPEC is not None and SPEC.loader is not None
EV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EV)

CHANNELS = sorted((REPO / "channels").glob("*/*.yaml"))
BRANDS = REPO / "brands"
SERIES = ROOT / "examples" / "performance-90d.synthetic.json"
EXPECTED = ROOT / "examples" / "decision-90d.expected.json"
A, B = "terakona-niche-a", "terakona-niche-b"


def series() -> dict:
    return json.loads(SERIES.read_text())


def portfolio() -> list[dict]:
    return EV.load_portfolio(CHANNELS, BRANDS)


def run(payload: dict) -> dict:
    return EV.evaluate(portfolio(), payload)


def by_code(result: dict) -> dict:
    return {row["code"]: row for row in result["channels"]}


def only(payload: dict, code: str) -> dict:
    """The series with one channel's data only: the other channel is not launched."""
    payload = copy.deepcopy(payload)
    kit = next(channel["kit"] for channel in portfolio() if channel["code"] == code)
    payload["observations"] = [row for row in payload["observations"] if row["brand"] == kit]
    for channel in payload["channels"]:
        if channel["code"] != code:
            channel.update(launch_date=None, pending_publications=[])
    return payload


# --- the B5 acceptance: a synthetic 90-day series ------------------------------------------------


def test_the_90_day_series_stops_one_channel_and_continues_the_other():
    rows = by_code(run(series()))
    assert rows[A]["decision"] == "continue" and rows[A]["reason"] == "day_90_met"
    assert rows[B]["decision"] == "stop" and rows[B]["reason"] == "day_90_missed"


def test_only_the_stopped_channels_pending_items_are_proposed_for_cancellation():
    result = run(series())
    assert result["proposal_only"] is True
    assert result["cancellation_proposals"] == [
        {"channel": B, "publication_id": 203, "state": "scheduled"},
        {"channel": B, "publication_id": 204, "state": "awaiting_creator"},
    ]


def test_output_is_deterministic_and_matches_the_committed_decision():
    first = EV.dumps(run(series()))
    shuffled = series()
    shuffled["observations"].reverse()
    shuffled["channels"].reverse()
    for channel in shuffled["channels"]:
        channel["pending_publications"].reverse()
    assert EV.dumps(run(shuffled)) == first
    assert first == EXPECTED.read_text().strip()


def test_g2_results_are_carried_per_checkpoint():
    rows = by_code(run(series()))
    day60, day90 = rows[A]["checkpoints"]
    assert (day60["day"], day90["day"]) == (60, 90)
    # pub 101 at day 60 = 3000, pub 102 = 2500 (point snapshots: latest per publication, summed).
    assert day60["metrics"]["views"] == {
        "status": "met", "observed": "5500", "direction": "gte", "unit": "count", "threshold": "5000"
    }  # fmt: skip
    # day 90: 4500 + the correction 3600 (not the superseded 3500); the 9900 after day 90 is out.
    assert day90["metrics"]["views"]["observed"] == "8100"
    assert rows[B]["checkpoints"][1]["metrics"]["watch_time_s"]["observed"] == "16000.5"
    assert rows[B]["checkpoints"][0]["metrics"]["watch_time_s"]["status"] == "unmeasured"


# --- decision rules -----------------------------------------------------------------------------


def test_a_day_60_miss_before_day_90_is_due_pivots():
    payload = only(series(), B)
    payload["as_of_date"] = "2026-08-10"  # day 60 (2026-07-31) due, day 90 (2026-08-30) not yet
    rows = by_code(run(payload))
    assert (rows[B]["decision"], rows[B]["reason"]) == ("pivot", "day_60_missed")
    assert run(payload)["cancellation_proposals"] == [], "a pivot proposes nothing"


def test_nothing_measured_continues_as_unmeasured():
    payload = only(series(), A)
    payload["observations"] = []
    rows = by_code(run(payload))
    assert (rows[A]["decision"], rows[A]["reason"]) == ("continue", "unmeasured")


def test_an_unlaunched_channel_continues_as_not_launched_and_skips_g2():
    payload = series()
    payload["observations"] = []
    for channel in payload["channels"]:
        channel["launch_date"] = None
    rows = by_code(run(payload))
    assert {row["reason"] for row in rows.values()} == {"not_launched"}
    assert all(row["checkpoints"] == [] for row in rows.values())


def test_day_60_met_and_day_90_not_due_continues():
    payload = only(series(), A)
    payload["as_of_date"] = "2026-08-10"
    rows = by_code(run(payload))
    assert (rows[A]["decision"], rows[A]["reason"]) == ("continue", "day_60_met")


def test_the_channels_on_miss_day_60_action_is_honoured(tmp_path):
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == B)
    target["stop_rule"]["on_miss"]["day_60"] = "stop"
    payload = only(series(), B)
    payload["as_of_date"] = "2026-08-10"
    rows = by_code(EV.evaluate(channels, payload))
    assert (rows[B]["decision"], rows[B]["reason"]) == ("stop", "day_60_missed")


def test_sales_are_additive_daily_counts():
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == A)
    target["stop_rule"]["metrics"] = [{"metric_id": "sales", "direction": "gte", "unit": "count", "threshold": "3"}]
    payload = only(series(), A)
    extra = dict(next(row for row in payload["observations"] if row["metric"] == "sales"), observation_id=17)
    extra.update(window_start="2026-08-02 00:00:00", window_end="2026-08-02 23:59:59", value=1)
    payload["observations"].append(extra)
    day90 = by_code(EV.evaluate(channels, payload))[A]["checkpoints"][1]
    assert day90["metrics"]["sales"]["observed"] == "3"
    assert day90["metrics"]["sales"]["status"] == "met"


def test_a_corrected_daily_count_replaces_the_original_not_adds_to_it():
    # Supersession matters most for additive metrics: summing both rows would double count.
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == A)
    target["stop_rule"]["metrics"] = [{"metric_id": "sales", "direction": "gte", "unit": "count", "threshold": "2"}]
    payload = only(series(), A)
    original = next(row for row in payload["observations"] if row["metric"] == "sales")
    payload["observations"].append(
        dict(original, observation_id=18, value=1, source="manual_correction", supersedes_id=original["observation_id"])
    )
    day90 = by_code(EV.evaluate(channels, payload))[A]["checkpoints"][1]
    assert day90["metrics"]["sales"] == {
        "status": "missed", "observed": "1", "direction": "gte", "unit": "count", "threshold": "2"
    }  # fmt: skip


def test_a_chained_correction_counts_once():
    # F0f allows correcting a correction: C corrects B corrects A must count C only.
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == A)
    target["stop_rule"]["metrics"] = [{"metric_id": "sales", "direction": "gte", "unit": "count", "threshold": "2"}]
    payload = only(series(), A)
    original = next(row for row in payload["observations"] if row["metric"] == "sales")
    first = dict(
        original, observation_id=18, value=1, source="manual_correction", supersedes_id=original["observation_id"]
    )
    second = dict(first, observation_id=19, supersedes_id=18)
    payload["observations"] += [first, second]
    day90 = by_code(EV.evaluate(channels, payload))[A]["checkpoints"][1]
    assert day90["metrics"]["sales"]["observed"] == "1"


def test_a_correction_cycle_refuses():
    payload = series()
    payload["observations"][4]["supersedes_id"] = 20  # 14 <-> 20
    refuses(payload, "correction cycle")


def test_a_correction_of_another_publication_refuses():
    payload = series()
    payload["observations"][5]["publication_id"] = 101
    refuses(payload, "different brand/metric/publication/window")


def test_a_correction_with_another_window_refuses():
    payload = series()
    payload["observations"][5]["window_end"] = "2026-09-01 09:00:00"
    refuses(payload, "different brand/metric/publication/window")


def test_sales_before_launch_do_not_count():
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == A)
    target["stop_rule"]["metrics"] = [{"metric_id": "sales", "direction": "gte", "unit": "count", "threshold": "1"}]
    payload = only(series(), A)
    sales = next(row for row in payload["observations"] if row["metric"] == "sales")
    sales.update(window_start="2026-05-20 00:00:00", window_end="2026-05-20 23:59:59")
    day90 = by_code(EV.evaluate(channels, payload))[A]["checkpoints"][1]
    assert day90["metrics"]["sales"]["status"] == "unmeasured"


def test_partial_evidence_at_day_90_falls_back_to_day_60_as_documented():
    # Pinned: day 90 met + unmeasured is neither a miss nor a full met, so the README's
    # first-match table moves on to day 60 (here also partial) and ends at `unmeasured`.
    channels = portfolio()
    target = next(channel for channel in channels if channel["code"] == A)
    target["stop_rule"]["metrics"].append({"metric_id": "watch_time_s", "direction": "gte", "unit": "seconds",
                                           "threshold": "1"})  # fmt: skip
    rows = by_code(EV.evaluate(channels, only(series(), A)))
    assert rows[A]["checkpoints"][1]["metrics"]["views"]["status"] == "met"
    assert (rows[A]["decision"], rows[A]["reason"]) == ("continue", "unmeasured")


def test_the_latest_of_several_corrections_wins():
    payload = only(series(), A)
    later = dict(next(row for row in payload["observations"] if row["observation_id"] == 20))
    later.update(observation_id=21, value=3700)
    payload["observations"].append(later)
    day90 = by_code(run(payload))[A]["checkpoints"][1]
    assert day90["metrics"]["views"]["observed"] == "8200"


def test_evidence_refs_are_opaque_and_stable():
    rows = by_code(run(series()))
    refs = [cp["evidence_ref"] for row in rows.values() for cp in row["checkpoints"] if cp["evidence_ref"]]
    assert refs and all(ref.startswith("f0f:") and "://" not in ref for ref in refs)
    assert refs == [cp["evidence_ref"] for row in by_code(run(series())).values() for cp in row["checkpoints"]
                    if cp["evidence_ref"]]  # fmt: skip


# --- refusals, by name ---------------------------------------------------------------------------


def refuses(payload: dict, match: str) -> None:
    with pytest.raises(EV.PortfolioError, match=match):
        run(payload)


def test_an_unknown_top_level_key_refuses():
    payload = series()
    payload["note"] = "x"
    refuses(payload, "keys invalid")


def test_an_unknown_observation_key_refuses():
    payload = series()
    payload["observations"][0]["buyer_email"] = "x@example.test"
    refuses(payload, r"observations\[0\] keys invalid")


def test_a_channel_outside_the_portfolio_refuses():
    payload = series()
    payload["channels"].append({"code": "terakona-niche-z", "launch_date": None, "pending_publications": []})
    refuses(payload, "channels do not match the portfolio")


def test_a_missing_portfolio_channel_refuses():
    payload = series()
    payload["channels"].pop()
    refuses(payload, "channels do not match the portfolio")


def test_an_observation_for_a_brand_outside_the_portfolio_refuses():
    payload = series()
    payload["observations"][0]["brand"] = "terakidz"
    refuses(payload, "outside the portfolio")


def test_a_terminal_publication_is_never_pending():
    payload = series()
    payload["channels"][1]["pending_publications"][0]["state"] = "published"
    refuses(payload, "is not a pending F10 state")


@pytest.mark.parametrize("value", [-1, True, "12", float("inf")])
def test_a_bad_value_refuses(value):
    payload = series()
    payload["observations"][0]["value"] = value
    refuses(payload, "value")


def test_a_point_metric_without_a_publication_refuses():
    payload = series()
    payload["observations"][0]["publication_id"] = None
    refuses(payload, "needs its publication_id")


def test_a_correction_of_an_unknown_observation_refuses():
    payload = series()
    payload["observations"][5]["supersedes_id"] = 999
    refuses(payload, "supersedes an observation not in the input")


@pytest.mark.parametrize(
    ("field", "value"),
    [("asset_key", "Budi Santoso"), ("digital_product_ref", "https://x.test/p"), ("window_end", "yesterday")],
)
def test_free_text_in_a_reference_field_refuses(field, value):
    payload = series()
    payload["observations"][0][field] = value
    refuses(payload, field)


def test_a_missing_or_retired_kit_refuses_by_name(tmp_path):
    brands = tmp_path / "brands"
    brands.mkdir()
    for path in BRANDS.glob("*.yaml"):
        (brands / path.name).write_text(path.read_text())
    kit_a = yaml.safe_load((BRANDS / f"{A}.yaml").read_text())
    (brands / f"{A}.yaml").write_text(yaml.safe_dump(dict(kit_a, status="retired")))
    (brands / f"{B}.yaml").unlink()
    with pytest.raises(EV.PortfolioError) as caught:
        EV.load_portfolio(CHANNELS, brands)
    assert f"channel {A}: kit {A} is retired" in str(caught.value)
    assert f"channel {B}: kit {B} is missing" in str(caught.value)


def test_a_schema_invalid_channel_refuses(tmp_path):
    channel = yaml.safe_load(CHANNELS[0].read_text())
    channel["formats"] = [{"format": "video", "enabled": True}]
    target = tmp_path / CHANNELS[0].parent.name / CHANNELS[0].name
    target.parent.mkdir()
    target.write_text(yaml.safe_dump(channel))
    with pytest.raises(EV.PortfolioError, match="channel.v1"):
        EV.load_portfolio([target], BRANDS)


def test_an_empty_portfolio_refuses():
    with pytest.raises(EV.PortfolioError, match="no channels"):
        EV.load_portfolio([], BRANDS)


# --- G2 is imported, not copied; no PII; never cancels ------------------------------------------


def test_g2_is_imported_from_its_single_reference_not_copied():
    g2 = EV.load_g2()
    assert Path(g2.__file__).resolve() == EV.G2_SCRIPT.resolve()
    assert EV.G2_SCRIPT.is_file()
    source = (ROOT / "evaluate.py").read_text()
    assert "def evaluate_scorecards" not in source
    assert source.count(EV.G2_SCRIPT.stem) == 1, "one reference, so the rename slice edits one line"


def test_the_output_carries_no_pii_or_urls():
    text = EV.dumps(run(series()))
    for needle in ("@", "://", "+62", "example.test", "company_id", "asset_key"):
        assert needle not in text, needle


def test_the_evaluator_never_mutates_its_input():
    payload = series()
    before = json.dumps(payload, sort_keys=True)
    run(payload)
    assert json.dumps(payload, sort_keys=True) == before


def test_the_module_has_no_cancel_or_write_path():
    source = (ROOT / "evaluate.py").read_text()
    for needle in ("action_cancel", "requests", "urllib", "xmlrpc", ".write_text(", 'open("w'):
        assert needle not in source, needle


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_prints_the_decision_and_exits_zero(capsys):
    code = EV.main(["--channels-dir", str(REPO / "channels"), "--brands-dir", str(BRANDS), str(SERIES)])
    out = capsys.readouterr()
    assert code == 0 and out.err == ""
    assert out.out.strip() == EXPECTED.read_text().strip()


def test_cli_refuses_bad_input_with_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1, "schema_version": 1}')
    code = EV.main(["--channels-dir", str(REPO / "channels"), str(bad)])
    assert code == 2
    assert capsys.readouterr().err.startswith("error: ")


def test_cli_refuses_an_oversized_input(tmp_path, capsys):
    big = tmp_path / "big.json"
    big.write_text(" " * (EV.MAX_INPUT_BYTES + 1))
    assert EV.main(["--channels-dir", str(REPO / "channels"), str(big)]) == 2
    assert "bytes" in capsys.readouterr().err
