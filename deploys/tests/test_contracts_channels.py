"""channel.v1 -- a niche channel as committed DATA (Track B, slice TB5; spec B5).

A channel is `channels/<brand>/<niche>.yaml`: the sub-brand kit it renders under, the F8 platform,
its weekly cadence, the formats it may use and its stop rule, written in the G2 scorecard's
threshold format (metric, direction, unit, decimal threshold; day-60/day-90 windows). The channel
portfolio evaluator (`ops/channel_portfolio/evaluate.py`) reads it; this module checks:

1. **Schema** -- the contract, its examples and one invalid fixture per refusal (video enabled
   before F6 per TB-D6, an unknown platform, a metric with the wrong unit, an unknown kit).
2. **Semantics** -- through the evaluator's own resolver (imported, never copied): every kit exists
   and is not retired, the code is `<dir>-<stem>`, each enabled format is one the platform accepts,
   and no two channels share a kit (a channel's F0f aggregates are attributed by kit code).
3. **The line** -- `product_lines/*-channels.yaml` lists one content piece per channel, under
   that channel's kit, one variant per enabled format.
4. **Brand-switched output (spec B5 e2e)** -- the same brief rendered under each channel kit gives
   different copy that passes each kit's pinned brand rules, and an injected forbidden phrase
   refuses by name (L3).
5. **G2 format** -- every committed stop rule is accepted by the G2 evaluator as it stands.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from contracts_lib import CONTRACTS, contracts_base_ref, load, pii_hits, registry, schema_property_names, validator_for

REPO = CONTRACTS.parent
SCHEMA = CONTRACTS / "channels" / "channel.v1.schema.json"
SCHEMA_ID = "https://kodeme.io/contracts/channels/channel.v1.schema.json"
EXAMPLES = CONTRACTS / "examples" / "channels"
CHANNELS_DIR = REPO / "channels"
BRANDS_DIR = REPO / "brands"
LINES_DIR = REPO / "product_lines"


def _import(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALUATE = _import("channel_portfolio_evaluate", REPO / "ops" / "channel_portfolio" / "evaluate.py")

# F8 vocabulary (kodemeio-odoo factory_content) and F6's video, which TB-D6 keeps disabled.
PLATFORMS = {"tiktok", "youtube", "instagram", "facebook"}
FORMATS = {"caption", "script", "carousel", "thread", "video"}
# F0f metrics with a verified source (factory_performance VERIFIED_PUBLICATION_METRICS + sales).
METRICS = {"views": "count", "watch_time_s": "seconds", "sales": "count"}
# The channel-level AI disclosure every F8 piece carries (content_channel_rules.yaml).
CHANNEL_AI_DISCLOSURE = "Konten ini dibuat dengan bantuan kecerdasan buatan (AI)."
TRIAL = "kit uji coba"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def errors(payload: dict) -> list:
    return list(validator_for(SCHEMA).iter_errors(payload))


def errors_at(payload: dict, prefix: tuple) -> list:
    return [error for error in errors(payload) if tuple(error.absolute_path)[: len(prefix)] == prefix]


def committed_channels() -> list[Path]:
    return sorted(CHANNELS_DIR.glob("*/*.yaml")) if CHANNELS_DIR.is_dir() else []


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


_CHANNELS = committed_channels()
_IDS = [f"{path.parent.name}/{path.stem}" for path in _CHANNELS]


# --- schema and examples -----------------------------------------------------


def test_schema_is_a_valid_self_contained_draft_2020_12_schema():
    schema = load(SCHEMA)
    validator_for(SCHEMA).check_schema(schema)
    assert "$ref" not in SCHEMA.read_text(), "keep the schema self-contained for contracts_lib's walkers"


def test_the_schema_is_registered_under_its_contracts_path():
    assert load(SCHEMA)["$id"] == SCHEMA_ID


def test_valid_example_validates_and_resolves():
    payload = example("channel.v1.valid.json")
    assert errors(payload) == []
    assert EVALUATE.channel_problems(payload, BRANDS_DIR) == []


def test_the_schema_enums_match_the_consumers_vocabularies():
    props = load(SCHEMA)["properties"]
    assert set(props["platform"]["enum"]) == PLATFORMS
    assert set(props["formats"]["items"]["properties"]["format"]["enum"]) == FORMATS
    metric = props["stop_rule"]["properties"]["metrics"]["items"]["properties"]
    assert set(metric["metric_id"]["enum"]) == set(METRICS)
    assert set(metric["unit"]["enum"]) == set(METRICS.values())
    assert set(metric["direction"]["enum"]) == {"gte", "lte"}
    assert set(EVALUATE.PLATFORM_FORMATS) == PLATFORMS


def test_stop_rule_metric_keys_are_exactly_g2s():
    # G2 closes each metric object on these four keys; anything else would fail there.
    metric = load(SCHEMA)["properties"]["stop_rule"]["properties"]["metrics"]["items"]
    assert set(metric["required"]) == set(metric["properties"]) == {"metric_id", "direction", "unit", "threshold"}
    assert metric["additionalProperties"] is False


# Each invalid fixture carries exactly one defect, at this instance path (None: schema-valid,
# refused by resolution).
INVALID_FIXTURES = {
    "channel.v1.invalid-video-enabled.json": ("formats",),
    "channel.v1.invalid-unknown-platform.json": ("platform",),
    "channel.v1.invalid-metric-unit.json": ("stop_rule", "metrics", 0),
    "channel.v1.invalid-unknown-kit.json": None,
}


def test_the_invalid_fixtures_are_exactly_the_declared_set():
    assert {path.name for path in EXAMPLES.glob("*.invalid-*.json")} == set(INVALID_FIXTURES)


@pytest.mark.parametrize("name", sorted(INVALID_FIXTURES))
def test_every_invalid_fixture_fails_for_its_declared_reason_only(name):
    payload = example(name)
    prefix = INVALID_FIXTURES[name]
    found = errors(payload)
    if prefix is None:
        assert found == []
        assert EVALUATE.channel_problems(payload, BRANDS_DIR)
        return
    assert found, f"{name} validated"
    stray = [error for error in found if tuple(error.absolute_path)[: len(prefix)] != prefix]
    assert not stray, [f"{list(error.absolute_path)}: {error.message}" for error in stray]


def test_video_is_an_allowed_format_that_stays_disabled_tb_d6():
    payload = example("channel.v1.valid.json")
    formats = {entry["format"]: entry["enabled"] for entry in payload["formats"]}
    assert formats.get("video") is False
    payload["formats"] = [
        dict(entry, enabled=True) if entry["format"] == "video" else entry for entry in payload["formats"]
    ]
    assert errors_at(payload, ("formats",))


def test_a_channel_needs_an_enabled_format():
    payload = example("channel.v1.valid.json")
    payload["formats"] = [dict(entry, enabled=False) for entry in payload["formats"]]
    assert errors_at(payload, ("formats",))


def test_a_format_is_listed_once():
    payload = example("channel.v1.valid.json")
    payload["formats"].append(dict(payload["formats"][0]))
    assert errors_at(payload, ("formats",))


@pytest.mark.parametrize("threshold", ["-1", "1e3", "abc", "1.", 5])
def test_a_threshold_is_a_non_negative_decimal_string(threshold):
    payload = example("channel.v1.valid.json")
    payload["stop_rule"]["metrics"][0]["threshold"] = threshold
    assert errors_at(payload, ("stop_rule", "metrics", 0, "threshold"))


def test_day_90_on_miss_is_stop():
    payload = example("channel.v1.valid.json")
    payload["stop_rule"]["on_miss"]["day_90"] = "pivot"
    assert errors_at(payload, ("stop_rule", "on_miss", "day_90"))
    payload["stop_rule"]["on_miss"]["day_90"] = "stop"
    payload["stop_rule"]["on_miss"]["day_60"] = "continue"
    assert errors_at(payload, ("stop_rule", "on_miss", "day_60"))


@pytest.mark.parametrize("cadence", [0, 22, 2.5])
def test_cadence_is_a_bounded_integer(cadence):
    payload = example("channel.v1.valid.json")
    payload["cadence_per_week"] = cadence
    assert errors_at(payload, ("cadence_per_week",))


@pytest.mark.parametrize(
    "path",
    [(), ("stop_rule",), ("stop_rule", "on_miss"), ("stop_rule", "metrics", 0), ("formats", 0)],
    ids=["top", "stop_rule", "on_miss", "metric", "format"],
)
def test_every_object_is_closed(path):
    payload = example("channel.v1.valid.json")
    node = payload
    for key in path:
        node = node[key]
    node["url"] = "https://example.test"
    assert errors_at(payload, path)


def test_no_pii_property_names_in_the_schema():
    names = schema_property_names(load(SCHEMA), registry())
    assert not pii_hits(names), pii_hits(names)


def test_schema_is_backward_compatible_with_base():
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = SCHEMA.relative_to(REPO).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=REPO, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    assert json.loads(shown.stdout) == load(SCHEMA)


# --- resolution: missing, retired and mismatched kits refuse by name ---------------------------


def _brands_with(tmp_path: Path, code: str, status: str | None) -> Path:
    brands = tmp_path / "brands"
    brands.mkdir()
    for path in BRANDS_DIR.glob("*.yaml"):
        (brands / path.name).write_text(path.read_text())
    if status is not None:
        kit = _yaml(BRANDS_DIR / "terakona.yaml")
        kit.update(code=code, status=status)
        (brands / f"{code}.yaml").write_text(yaml.safe_dump(kit))
    return brands


def test_a_missing_kit_refuses_by_name(tmp_path):
    payload = example("channel.v1.valid.json")
    payload["kit"] = "terakona-niche-z"
    problems = EVALUATE.channel_problems(payload, _brands_with(tmp_path, "unused", None))
    assert problems == [f"channel {payload['code']}: kit terakona-niche-z is missing"]


def test_a_retired_kit_refuses_by_name(tmp_path):
    payload = example("channel.v1.valid.json")
    payload["kit"] = "terakona-niche-z"
    problems = EVALUATE.channel_problems(payload, _brands_with(tmp_path, "terakona-niche-z", "retired"))
    assert problems == [f"channel {payload['code']}: kit terakona-niche-z is retired"]


def test_a_format_the_platform_does_not_accept_refuses_by_name():
    payload = example("channel.v1.valid.json")
    payload["platform"] = "youtube"
    payload["formats"] = [{"format": "carousel", "enabled": True}, {"format": "video", "enabled": False}]
    assert errors(payload) == []
    problems = EVALUATE.channel_problems(payload, BRANDS_DIR)
    assert problems == [f"channel {payload['code']}: platform youtube does not accept format carousel"]


def test_a_format_listed_twice_with_different_flags_refuses_by_name():
    payload = example("channel.v1.valid.json")
    payload["formats"].append({"format": "caption", "enabled": False})
    assert errors(payload) == [], "distinct entries pass uniqueItems; the resolver refuses them"
    problems = EVALUATE.channel_problems(payload, BRANDS_DIR)
    assert problems == [f"channel {payload['code']}: format caption is listed twice"]


def test_the_retired_status_exists_in_the_brand_kit_contract():
    # The refusal above is only meaningful if a kit can actually be retired.
    assert "retired" in load(CONTRACTS / "brands" / "brand_kit.v1.schema.json")["properties"]["status"]["enum"]


# --- every committed channel ---------------------------------------------------------------------


def test_the_b5_portfolio_has_two_example_niches():
    assert len(_CHANNELS) >= 2, "spec B5: channels/<brand>/<channel>.yaml for two example niches"


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_committed_channel_validates_and_resolves(path):
    channel = _yaml(path)
    assert errors(channel) == [], [error.message for error in errors(channel)]
    assert channel["code"] == f"{path.parent.name}-{path.stem}", f"{path}: code must be <dir>-<stem>"
    assert EVALUATE.channel_problems(channel, BRANDS_DIR) == []


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_committed_channel_header_marks_example_and_placeholder(path):
    header = path.read_text().split("\n\n", 1)[0]
    assert "EXAMPLE CONTENT" in header and "PLACEHOLDER" in header, f"{path} header"


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_committed_channel_keeps_video_disabled_tb_d6(path):
    formats = {entry["format"]: entry["enabled"] for entry in _yaml(path)["formats"]}
    assert formats.get("video") is False, "video is listed and disabled until the founder lifts TB-D6"
    enabled = {name for name, on in formats.items() if on}
    assert enabled and enabled <= {"caption", "script", "carousel"}, enabled


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_committed_channel_kit_is_a_draft_placeholder_with_the_trial_disclaimer(path):
    kit = _yaml(BRANDS_DIR / f"{_yaml(path)['kit']}.yaml")
    assert kit["status"] == "draft"
    assert any(TRIAL in disclaimer.lower() for disclaimer in kit["rules"]["required_disclaimers"])
    assert kit["reviewers"]["expert_logins"] == []


def test_the_committed_portfolio_loads_and_no_two_channels_share_a_kit():
    channels = EVALUATE.load_portfolio(_CHANNELS, BRANDS_DIR)
    kits = [channel["kit"] for channel in channels]
    assert len(kits) == len(set(kits))


def test_two_channels_sharing_a_kit_refuse(tmp_path):
    first, second = (_yaml(path) for path in _CHANNELS[:2])
    second["kit"] = first["kit"]
    target = tmp_path / _CHANNELS[1].parent.name
    target.mkdir()
    (target / _CHANNELS[1].name).write_text(yaml.safe_dump(second))
    with pytest.raises(EVALUATE.PortfolioError, match="share kit"):
        EVALUATE.load_portfolio([_CHANNELS[0], target / _CHANNELS[1].name], BRANDS_DIR)


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_committed_stop_rule_is_accepted_by_g2(path):
    channel = _yaml(path)
    g2 = EVALUATE.load_g2()
    metrics = channel["stop_rule"]["metrics"]
    empty = dict.fromkeys(metric["metric_id"] for metric in metrics)
    checkpoint = {"observed_on": None, "observations": empty, "evidence_ref": None}
    result = g2.evaluate_scorecards(
        {
            "schema_version": 1,
            "as_of_date": "2026-09-28",
            "products": [
                {
                    "product_id": channel["code"],
                    "launch_date": "2026-01-01",
                    "currency": "IDR",
                    "metrics": metrics,
                    "checkpoints": [
                        dict(checkpoint, day=60, due_date="2026-03-02"),
                        dict(checkpoint, day=90, due_date="2026-04-01"),
                    ],
                }
            ],
        }
    )
    statuses = {row["status"] for cp in result["products"][0]["checkpoints"] for row in cp["metrics"].values()}
    assert statuses == {"unmeasured"}


# --- the channel line ------------------------------------------------------------------------


def _channel_lines() -> list[Path]:
    """Lines whose content pieces point at channels (a `content_piece` ref equal to a channel code)."""
    codes = {_yaml(path)["code"] for path in _CHANNELS}
    lines = []
    for path in sorted(LINES_DIR.glob("*.yaml")):
        refs = {item["ref"] for item in _yaml(path)["items"] if item["kind"] == "content_piece"}
        if refs & codes:
            lines.append(path)
    return lines


def test_the_channel_line_is_committed():
    assert "terakona-channels.yaml" in [path.name for path in _channel_lines()]


def test_the_channel_line_lists_one_piece_per_channel_under_its_kit():
    line = _yaml(LINES_DIR / "terakona-channels.yaml")
    channels = {channel["code"]: channel for channel in EVALUATE.load_portfolio(_CHANNELS, BRANDS_DIR)}
    pieces = {item["ref"]: item for item in line["items"]}
    assert set(pieces) == set(channels), "one content_piece item per committed channel"
    for code, item in pieces.items():
        channel = channels[code]
        assert item["kind"] == "content_piece"
        assert item["kit"] == channel["kit"], f"{code}: the piece renders under the channel kit"
        assert item["publish"] == "none" and item["price_idr"] == 0
        enabled = {entry["format"] for entry in channel["formats"] if entry["enabled"]}
        assert {variant["key"] for variant in item["variants"]} == enabled, f"{code}: one variant per format"
    assert line["launch"] == [], "launch briefs carry no kit; the channel pieces are the items"


# --- brand-switched output, L3 (pinned brand checks) -------------------------------------------


def _phrase_re(phrase: str) -> re.Pattern:
    words = [re.escape(word) for word in str(phrase).split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def _normalise(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def content_brand_rules(texts: list[str], rules: dict) -> list[tuple[str, bool, str]]:
    """Pinned copy of the phrase/disclaimer/AI-disclosure half of kodemeio-odoo factory_content
    `content_brand_rules` (message shapes included): a generated piece carries every kit
    disclaimer and the kit's own AI disclosure, and never a forbidden phrase."""
    results = []
    for phrase in rules.get("forbidden_phrases") or []:
        if not str(phrase).split():
            continue
        for index, text in enumerate(texts):
            match = _phrase_re(phrase).search(text)
            if match:
                results.append(
                    ("content_brand_rules", False, f"copy[{index}]@{match.start()}: forbidden phrase {phrase!r}")
                )
    body = _normalise(" ".join(texts))
    for number, disclaimer in enumerate(rules.get("required_disclaimers") or []):
        if _normalise(disclaimer) not in body:
            results.append(("content_brand_rules", False, f"required disclaimer #{number + 1} missing from the copy"))
    disclosure = _normalise(rules.get("ai_disclosure"))
    if not disclosure or disclosure not in body:
        results.append(("content_brand_rules", False, "generated copy without the kit's AI disclosure"))
    return results or [("content_brand_rules", True, "the brand kit governs this piece")]


# EXAMPLE CONTENT: the one synthetic brief every channel renders (the F8 fake generator's copy is
# K2's to wire). Fixed text, no PII, no URL.
BRIEF = "Tiga ide konten minggu ini untuk kanal kami. Simpan dan coba satu per satu."


def piece(code: str, *, brief: str = BRIEF) -> tuple[list[str], str]:
    """The copy a channel's F8 piece carries under its kit, and the brand-switched snapshot hash."""
    channel = next(channel for channel in EVALUATE.load_portfolio(_CHANNELS, BRANDS_DIR) if channel["code"] == code)
    kit = _yaml(BRANDS_DIR / f"{channel['kit']}.yaml")
    texts = [brief, *kit["rules"]["required_disclaimers"], kit["rules"]["ai_disclosure"], CHANNEL_AI_DISCLOSURE]
    snapshot = {"kit": kit["code"], "version": kit["version"], "colors": kit["colors"], "fonts": kit["fonts"],
                "platform": channel["platform"], "copy": texts}  # fmt: skip
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return texts, digest


def test_brand_switched_output_differs_per_channel_kit():
    codes = [channel["code"] for channel in EVALUATE.load_portfolio(_CHANNELS, BRANDS_DIR)]
    rendered = {code: piece(code) for code in codes}
    copies = {code: "\n".join(texts) for code, (texts, _digest) in rendered.items()}
    digests = {digest for _texts, digest in rendered.values()}
    assert len(set(copies.values())) == len(codes), "each channel kit must switch the copy"
    assert len(digests) == len(codes), "each channel kit must switch the snapshot"


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_each_channel_piece_passes_its_kits_brand_rules(path):
    channel = _yaml(path)
    texts, _digest = piece(channel["code"])
    rules = _yaml(BRANDS_DIR / f"{channel['kit']}.yaml")["rules"]
    assert content_brand_rules(texts, rules) == [("content_brand_rules", True, "the brand kit governs this piece")]
    assert any(TRIAL in text.lower() for text in texts), "L2: a placeholder kit's piece says kit uji coba"


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_l3_an_injected_forbidden_phrase_refuses_by_name_without_echoing_the_copy(path):
    channel = _yaml(path)
    rules = _yaml(BRANDS_DIR / f"{channel['kit']}.yaml")["rules"]
    phrase = rules["forbidden_phrases"][0]
    secret_copy = f"Konten uji {phrase.upper()} di sini"
    texts, _digest = piece(channel["code"], brief=secret_copy)
    failed = [row for row in content_brand_rules(texts, rules) if not row[1]]
    assert [(code, message.split(": ", 1)[1]) for code, _passed, message in failed] == [
        ("content_brand_rules", f"forbidden phrase {phrase!r}")
    ]
    assert secret_copy not in failed[0][2]


@pytest.mark.parametrize("path", _CHANNELS, ids=_IDS)
def test_a_piece_without_the_trial_disclaimer_refuses(path):
    channel = _yaml(path)
    rules = _yaml(BRANDS_DIR / f"{channel['kit']}.yaml")["rules"]
    texts, _digest = piece(channel["code"])
    stripped = [text for text in texts if TRIAL not in text.lower()]
    assert any("required disclaimer" in row[2] for row in content_brand_rules(stripped, rules) if not row[1])
