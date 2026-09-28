#!/usr/bin/env python3
"""Channel portfolio evaluator: channel stop rules + F0f-shaped aggregates -> continue|pivot|stop.

Offline and read-only. It reads the committed `channel.v1` files and one explicit JSON input (the
product-line harness `performance` step writes it from `factory.performance.snapshot`; the
examples are synthetic), feeds each channel's stop rule and aggregated observations to the G2
scorecard evaluator -- imported from its one reference below, never copied -- and maps G2's
met/missed/unmeasured checkpoints to one decision per channel. For a stopped channel it LISTS the
pending F10 publications the founder may cancel; it never cancels, writes or calls anything.

Decision rule (per channel, from G2's checkpoints; `on_miss` comes from the channel file):

* not launched (no `launch_date`)            -> continue, `not_launched` (G2 is not called)
* day 90 due and any stop metric missed      -> `on_miss.day_90` (always stop), `day_90_missed`
* day 90 due and every stop metric met       -> continue, `day_90_met`
* day 60 due and any stop metric missed      -> `on_miss.day_60` (pivot|stop), `day_60_missed`
* day 60 due and every stop metric met       -> continue, `day_60_met`
* otherwise                                  -> continue, `unmeasured`

Aggregation (F0f semantics): a publication's `views`/`watch_time_s` rows are POINT snapshots of a
running total, so each publication contributes its latest row at or before the checkpoint's due
date, summed over publications; `sales` rows are daily counts and add up. A correction row
(`supersedes_id`) replaces the row it supersedes; of several, the highest `observation_id` wins.
Output contains codes, ids, states and numbers only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from math import isfinite
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[2]
# The G2 scorecard evaluator. Its ONE reference: a later slice renames the script by editing this line.
G2_SCRIPT = REPO / "ops" / "scripts" / "teracorp_ops_metrics.py"
CHANNEL_SCHEMA = REPO / "contracts" / "channels" / "channel.v1.schema.json"
DEFAULT_BRANDS = REPO / "brands"
MAX_INPUT_BYTES = 1_048_576

# F8 channel -> the piece kinds it accepts: a pinned copy of kodemeio-odoo
# factory_content/data/content_channel_rules.yaml `kinds`. `video` is F6's and is refused by the
# contract while TB-D6 holds, so it never reaches this table.
PLATFORM_FORMATS = {
    "tiktok": {"caption", "script", "carousel"},
    "youtube": {"caption", "script"},
    "instagram": {"caption", "script", "carousel"},
    "facebook": {"caption", "script", "carousel"},
}
# F10 (content_publish_base PUBLICATION_STATES) minus the terminal states.
PENDING_STATES = {
    "draft",
    "preparing",
    "prepare_unknown",
    "prepared",
    "awaiting_creator",
    "scheduled",
    "processing",
    "publish_unknown",
}
POINT_METRICS = {"views", "watch_time_s"}
ADDITIVE_METRICS = {"sales"}
UNITS = {"views": "count", "watch_time_s": "seconds", "sales": "count"}
SOURCES = {"content_publish_base", "factory_digital.purchase", "manual_correction"}
CHECKPOINT_DAYS = (60, 90)

_CODE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PRODUCT_REF = re.compile(r"^digital\.product,[1-9][0-9]{0,17}$")
_STAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}$")
_TOP_KEYS = {"schema_version", "as_of_date", "currency", "channels", "observations"}
_CHANNEL_KEYS = {"code", "launch_date", "pending_publications"}
_PENDING_KEYS = {"publication_id", "state"}
_OBSERVATION_KEYS = {
    "observation_id",
    "publication_id",
    "company_id",
    "brand",
    "asset_key",
    "digital_product_ref",
    "metric",
    "value",
    "unit",
    "source",
    "window_start",
    "window_end",
    "freshness",
    "coverage",
    "supersedes_id",
}


class PortfolioError(ValueError):
    """A channel file or evaluator input is invalid; the message names what and where."""


@cache
def load_g2():
    """The G2 module, imported from its single reference (never copied)."""
    spec = importlib.util.spec_from_file_location("g2_scorecards", G2_SCRIPT)
    if spec is None or spec.loader is None or not G2_SCRIPT.is_file():
        raise PortfolioError(f"G2 scorecard evaluator not found at {G2_SCRIPT.relative_to(REPO)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _channel_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CHANNEL_SCHEMA.read_text()))


# --- channels -----------------------------------------------------------------------------------


def channel_problems(channel: dict, brands_dir: Path) -> list[str]:
    """Everything wrong with a schema-valid channel that only the committed repo can reveal."""
    code, kit = channel["code"], channel["kit"]
    problems = []
    kit_path = Path(brands_dir) / f"{kit}.yaml"
    if not kit_path.is_file():
        problems.append(f"channel {code}: kit {kit} is missing")
    else:
        brand_kit = yaml.safe_load(kit_path.read_text()) or {}
        if brand_kit.get("code") != kit:
            problems.append(f"channel {code}: kit file {kit}.yaml carries code {brand_kit.get('code')!r}")
        if brand_kit.get("status") == "retired":
            problems.append(f"channel {code}: kit {kit} is retired")
    names = [entry["format"] for entry in channel["formats"]]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(f"channel {code}: format {name} is listed twice")
    accepted = PLATFORM_FORMATS.get(channel["platform"], set())
    for entry in channel["formats"]:
        if entry["enabled"] and entry["format"] not in accepted:
            problems.append(f"channel {code}: platform {channel['platform']} does not accept format {entry['format']}")
    return problems


def load_portfolio(paths: list[Path], brands_dir: Path = DEFAULT_BRANDS) -> list[dict]:
    """Load, validate and resolve every channel file; refuse the whole portfolio on any problem."""
    if not paths:
        raise PortfolioError("no channels: pass at least one channel.v1 file")
    channels, problems = [], []
    for path in sorted(Path(p) for p in paths):
        channel = yaml.safe_load(path.read_text())
        found = sorted(_channel_validator().iter_errors(channel), key=lambda error: list(error.absolute_path))
        if found:
            where = "/".join(str(part) for part in found[0].absolute_path) or "<root>"
            problems.append(f"{path.parent.name}/{path.name}: not a valid channel.v1 at {where}")
            continue
        if channel["code"] != f"{path.parent.name}-{path.stem}":
            problems.append(f"{path.parent.name}/{path.name}: code must be {path.parent.name}-{path.stem}")
        problems += channel_problems(channel, brands_dir)
        channels.append(channel)
    seen: dict[str, str] = {}
    for channel in channels:
        if channel["code"] in seen.values():
            problems.append(f"channel {channel['code']} is defined twice")
        other = seen.get(channel["kit"])
        if other:
            problems.append(f"channels {other} and {channel['code']} share kit {channel['kit']}")
        seen[channel["kit"]] = channel["code"]
    if problems:
        raise PortfolioError("; ".join(problems))
    return channels


# --- input validation ---------------------------------------------------------------------------


def _closed(value: Any, where: str, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        got = sorted(value) if type(value) is dict else type(value).__name__
        raise PortfolioError(f"{where} keys invalid (expected {sorted(keys)}, got {got})")
    return value


def _date(value: Any, where: str) -> date:
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        raise PortfolioError(f"{where} must be a canonical YYYY-MM-DD date")
    return parsed


def _positive_id(value: Any, where: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value <= 0:
        raise PortfolioError(f"{where} must be a positive integer id")
    return value


def _match(value: Any, pattern: re.Pattern, where: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PortfolioError(f"{where} is not a valid reference")
    return value


def _observation(raw: Any, where: str, kits: set[str]) -> dict:
    row = _closed(raw, where, _OBSERVATION_KEYS)
    _positive_id(row["observation_id"], f"{where}.observation_id")
    _positive_id(row["company_id"], f"{where}.company_id")
    _positive_id(row["supersedes_id"], f"{where}.supersedes_id", nullable=True)
    _match(row["asset_key"], _KEY, f"{where}.asset_key", nullable=True)
    _match(row["digital_product_ref"], _PRODUCT_REF, f"{where}.digital_product_ref", nullable=True)
    _match(row["window_start"], _STAMP, f"{where}.window_start")
    _match(row["window_end"], _STAMP, f"{where}.window_end")
    _date(row["window_end"][:10], f"{where}.window_end")
    if row["brand"] not in kits:
        raise PortfolioError(f"{where}.brand is outside the portfolio")
    if row["metric"] not in UNITS:
        raise PortfolioError(f"{where}.metric is not a verified F0f metric")
    if row["unit"] != UNITS[row["metric"]]:
        raise PortfolioError(f"{where}.unit does not match {row['metric']}")
    if row["source"] not in SOURCES:
        raise PortfolioError(f"{where}.source is not an F0f source")
    if row["freshness"] not in {"fresh", "stale"} or row["coverage"] not in {"measured", "unattributed"}:
        raise PortfolioError(f"{where}.freshness/coverage is not an F0f value")
    value = row["value"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value) or value < 0:
        raise PortfolioError(f"{where}.value must be a finite non-negative number")
    if row["metric"] in POINT_METRICS:
        if row["publication_id"] is None:
            raise PortfolioError(f"{where} is a point snapshot and needs its publication_id")
        _positive_id(row["publication_id"], f"{where}.publication_id")
    else:
        _positive_id(row["publication_id"], f"{where}.publication_id", nullable=True)
    return row


def _validate_input(payload: Any, channels: list[dict]) -> tuple[date, str, dict, list[dict]]:
    root = _closed(payload, "input", _TOP_KEYS)
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise PortfolioError("input.schema_version must be integer 1")
    as_of = _date(root["as_of_date"], "input.as_of_date")
    currency = root["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise PortfolioError("input.currency must be a three-letter uppercase currency code")
    if type(root["channels"]) is not list or type(root["observations"]) is not list:
        raise PortfolioError("input.channels and input.observations must be lists")

    entries: dict[str, dict] = {}
    for index, raw in enumerate(root["channels"]):
        where = f"channels[{index}]"
        entry = _closed(raw, where, _CHANNEL_KEYS)
        code = _match(entry["code"], _CODE, f"{where}.code")
        if code in entries:
            raise PortfolioError(f"{where}.code {code} is listed twice")
        if entry["launch_date"] is not None:
            _date(entry["launch_date"], f"{where}.launch_date")
        if type(entry["pending_publications"]) is not list:
            raise PortfolioError(f"{where}.pending_publications must be a list")
        for number, pending in enumerate(entry["pending_publications"]):
            pwhere = f"{where}.pending_publications[{number}]"
            _closed(pending, pwhere, _PENDING_KEYS)
            _positive_id(pending["publication_id"], f"{pwhere}.publication_id")
            if pending["state"] not in PENDING_STATES:
                raise PortfolioError(f"{pwhere}.state is not a pending F10 state")
        entries[code] = entry
    expected = {channel["code"] for channel in channels}
    if set(entries) != expected:
        raise PortfolioError(
            f"input channels do not match the portfolio (missing={sorted(expected - set(entries))}, "
            f"unknown={sorted(set(entries) - expected)})"
        )

    kits = {channel["kit"] for channel in channels}
    rows = [_observation(raw, f"observations[{index}]", kits) for index, raw in enumerate(root["observations"])]
    ids = [row["observation_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise PortfolioError("observations repeat an observation_id")
    for index, row in enumerate(rows):
        if row["supersedes_id"] is not None and row["supersedes_id"] not in ids:
            raise PortfolioError(f"observations[{index}] supersedes an observation not in the input")
    return as_of, currency, entries, rows


# --- aggregation and decision -------------------------------------------------------------------


def _effective(rows: list[dict]) -> list[dict]:
    """Drop superseded rows; of several corrections of one row, the highest observation_id wins."""
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["supersedes_id"] or row["observation_id"], []).append(row)
    return [max(group, key=lambda row: row["observation_id"]) for group in groups.values()]


def _aggregate(rows: list[dict], metric: str, due: date) -> tuple[str | None, list[int]]:
    counted = [row for row in rows if row["metric"] == metric and date.fromisoformat(row["window_end"][:10]) <= due]
    if not counted:
        return None, []
    if metric in POINT_METRICS:
        latest: dict[int, dict] = {}
        for row in counted:
            current = latest.get(row["publication_id"])
            if current is None or (row["window_end"], row["observation_id"]) > (
                current["window_end"],
                current["observation_id"],
            ):
                latest[row["publication_id"]] = row
        counted = list(latest.values())
    total = sum((Decimal(str(row["value"])) for row in counted), Decimal(0))
    text = format(total.normalize(), "f") if total else "0"
    return text, sorted(row["observation_id"] for row in counted)


def _decide(channel: dict, checkpoints: list[dict]) -> tuple[str, str]:
    on_miss = channel["stop_rule"]["on_miss"]
    by_day = {checkpoint["day"]: checkpoint for checkpoint in checkpoints}
    for day in (90, 60):
        checkpoint = by_day.get(day)
        if not checkpoint or checkpoint["observed_on"] is None:
            continue
        statuses = {row["status"] for row in checkpoint["metrics"].values()}
        if "missed" in statuses:
            return on_miss[f"day_{day}"], f"day_{day}_missed"
        if statuses == {"met"}:
            return "continue", f"day_{day}_met"
    return "continue", "unmeasured"


def evaluate(channels: list[dict], payload: Any) -> dict:
    """The decision document for one portfolio and one input. Pure: no I/O, no mutation."""
    as_of, currency, entries, rows = _validate_input(payload, channels)
    effective = _effective(rows)
    g2 = load_g2()
    products, launched = [], {}
    for channel in sorted(channels, key=lambda item: item["code"]):
        entry = entries[channel["code"]]
        if entry["launch_date"] is None:
            continue
        launch = date.fromisoformat(entry["launch_date"])
        mine = [row for row in effective if row["brand"] == channel["kit"]]
        checkpoints = []
        for day in CHECKPOINT_DAYS:
            due = launch + timedelta(days=day)
            observations: dict[str, str | None] = {}
            used: list[int] = []
            for metric in channel["stop_rule"]["metrics"]:
                value, ids = _aggregate(mine, metric["metric_id"], due) if due <= as_of else (None, [])
                observations[metric["metric_id"]] = value
                used += ids
            measured = any(value is not None for value in observations.values())
            digest = hashlib.sha256(",".join(str(i) for i in sorted(set(used))).encode()).hexdigest()[:16]
            checkpoints.append(
                {
                    "day": day,
                    "due_date": due.isoformat(),
                    "observed_on": due.isoformat() if measured else None,
                    "observations": observations,
                    "evidence_ref": f"f0f:{channel['code']}:d{day}:{digest}" if measured else None,
                }
            )
        products.append(
            {
                "product_id": channel["code"],
                "launch_date": launch.isoformat(),
                "currency": currency,
                "metrics": [dict(metric) for metric in channel["stop_rule"]["metrics"]],
                "checkpoints": checkpoints,
            }
        )
        launched[channel["code"]] = channel
    scored = {}
    if products:
        try:
            result = g2.evaluate_scorecards(
                {"schema_version": 1, "as_of_date": as_of.isoformat(), "products": products}
            )
        except g2.InputError as exc:
            raise PortfolioError(f"G2 refused the scorecard: {exc}") from exc
        scored = {product["product_id"]: product["checkpoints"] for product in result["products"]}

    decisions, proposals = [], []
    for channel in sorted(channels, key=lambda item: item["code"]):
        code = channel["code"]
        entry = entries[code]
        if code not in launched:
            decision, reason, checkpoints = "continue", "not_launched", []
        else:
            checkpoints = scored[code]
            decision, reason = _decide(channel, checkpoints)
        decisions.append(
            {
                "code": code,
                "kit": channel["kit"],
                "platform": channel["platform"],
                "launch_date": entry["launch_date"],
                "decision": decision,
                "reason": reason,
                "checkpoints": checkpoints,
            }
        )
        if decision == "stop":
            proposals += [
                {"channel": code, "publication_id": pending["publication_id"], "state": pending["state"]}
                for pending in entry["pending_publications"]
            ]
    proposals.sort(key=lambda row: (row["channel"], row["publication_id"]))
    return {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "proposal_only": True,
        "channels": decisions,
        "cancellation_proposals": proposals,
    }


def dumps(result: dict) -> str:
    """The one serialisation: sorted keys, compact separators, byte-stable across runs."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _read_input(path: Path) -> Any:
    g2 = load_g2()
    with path.open("rb") as stream:
        raw_bytes = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw_bytes) > MAX_INPUT_BYTES:
        raise PortfolioError(f"input file must not exceed {MAX_INPUT_BYTES} bytes")
    try:
        raw = raw_bytes.decode("utf-8")
        g2._enforce_json_depth(raw)
        return json.loads(raw, object_pairs_hook=g2._reject_duplicate_keys, parse_int=g2._parse_json_integer)
    except g2.InputError as exc:
        raise PortfolioError(str(exc)) from exc
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PortfolioError("input must be valid bounded UTF-8 JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--channels-dir", type=Path, help="a directory of channel.v1 files (<dir>/<brand>/<niche>.yaml)"
    )
    source.add_argument("--channel", type=Path, action="append", help="one channel.v1 file (repeatable)")
    parser.add_argument("--brands-dir", type=Path, default=DEFAULT_BRANDS, help="the brand kit directory")
    parser.add_argument("input", type=Path, help="explicit evaluator input JSON path")
    args = parser.parse_args(argv)
    try:
        paths = sorted(args.channels_dir.glob("*/*.yaml")) if args.channels_dir else args.channel
        channels = load_portfolio(paths, args.brands_dir)
        result = evaluate(channels, _read_input(args.input))
    except (OSError, PortfolioError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
