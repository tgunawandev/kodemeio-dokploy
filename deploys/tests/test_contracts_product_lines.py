"""product_line.v1 -- a product line as committed DATA (Track B, slice K1; spec 3.2).

A line is `product_lines/<id>.yaml`: the brand kit it renders under, the items it sells or ships
(each pointing at a committed template, e-book or course by id, with its variants, price and the
publish path), and the launch content (F8 briefs). One file per line, so parallel slices never
edit the same file. The product-line harness (kodemeio-odoo `bin/product-line-acceptance`) reads it.

Two layers are checked, and the second one is the point:

1. **Schema** -- the contract, the examples under `contracts/examples/product_lines/` and one
   invalid fixture per refusal (raw URL, unknown kind, negative price, unknown kit).
2. **Semantics the schema cannot express** -- every kit exists; every template/ebook/course ref
   resolves to a committed directory whose own `id` matches and which is approved for the item's
   kit; every variant only sets variables the source declares, with a value of the declared
   type (and one of the declared `enum` values); refs, variant keys and brief keys are unique;
   and no string anywhere in a line is a URL. Checked for the valid example AND every committed
   `product_lines/*.yaml`.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess

import pytest
import yaml
from contracts_lib import CONTRACTS, contracts_base_ref, load, pii_hits, registry, schema_property_names, validator_for

REPO = CONTRACTS.parent
SCHEMA = CONTRACTS / "product_lines" / "product_line.v1.schema.json"
SCHEMA_ID = "https://kodeme.io/contracts/product_lines/product_line.v1.schema.json"
EXAMPLES = CONTRACTS / "examples" / "product_lines"
LINES_DIR = REPO / "product_lines"
BRANDS_DIR = REPO / "brands"

# kind -> (directory, source file) of the committed source a ref names. `content_piece` and
# `physical` have no committed factory source directory: their ref is an opaque key.
SOURCES = {
    "template": ("templates", "template.yaml"),
    "ebook": ("ebooks", "ebook.yaml"),
    "course": ("courses", "course.yaml"),
}
KINDS = {"template", "ebook", "course", "content_piece", "physical"}
PUBLISH = {"factory_digital", "digital", "none"}
# The seeded `digital.kind` codes (kodemeio-odoo digital_base/data/digital_kind_data.xml).
DIGITAL_KINDS = {"book", "whitepaper", "template_pack", "prompt_pack", "course", "planner"}
# F8 (factory_content): the committed channels and the piece kinds (`KINDS` in
# factory_content_piece.py). `platform` maps to the brief's `channel`, `shape` to its `kind`.
PLATFORMS = {"tiktok", "youtube", "instagram", "facebook"}
SHAPES = {"caption", "script", "carousel", "thread"}
# F10 (content_publish_base PUBLICATION_MODES).
PUBLISH_MODES = {"creator_handoff", "prepared_container", "private_remote_asset", "direct_publish"}

URL_RE = re.compile(r"(://|^[Ww]{3}\.|^(data|javascript|mailto|file):)")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def errors(payload: dict) -> list:
    return list(validator_for(SCHEMA).iter_errors(payload))


def errors_at(payload: dict, prefix: tuple) -> list:
    return [error for error in errors(payload) if tuple(error.absolute_path)[: len(prefix)] == prefix]


def committed_lines() -> list:
    return sorted(LINES_DIR.glob("*.yaml")) if LINES_DIR.is_dir() else []


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _variable_problem(variable: dict, value) -> str | None:
    kind = variable["type"]
    if kind == "int":
        return None if isinstance(value, int) and not isinstance(value, bool) else "is not an int"
    if not isinstance(value, str):
        return f"is not a {kind} string"
    if kind == "enum" and value not in variable["enum"]:
        return f"is not one of {variable['enum']}"
    if kind == "date":
        if not DATE_RE.match(value):
            return "is not a YYYY-MM-DD date"
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            return "is not a calendar date"
    return None


def line_problems(line: dict) -> list[str]:
    """Everything wrong with a schema-valid line that only the committed repo can reveal. A
    consumer (the product-line harness) refuses the same things by name."""
    problems = []
    kits = {path.stem for path in BRANDS_DIR.glob("*.yaml")}
    if line["brand_kit"] not in kits:
        problems.append(f"kit {line['brand_kit']} is not a committed brand kit")

    seen_items = set()
    for index, item in enumerate(line["items"]):
        where = f"items[{index}] {item['kind']}:{item['ref']}"
        if (item["kind"], item["ref"]) in seen_items:
            problems.append(f"{where} is listed twice")
        seen_items.add((item["kind"], item["ref"]))

        kit = item.get("kit", line["brand_kit"])
        if kit not in kits:
            problems.append(f"{where} kit {kit} is not a committed brand kit")

        keys = [variant["key"] for variant in item["variants"]]
        if len(keys) != len(set(keys)):
            problems.append(f"{where} repeats a variant key")

        if item["kind"] not in SOURCES:
            for variant in item["variants"]:
                if variant.get("vars"):
                    problems.append(f"{where} variant {variant['key']} sets vars on a source-less kind")
            continue

        directory, filename = SOURCES[item["kind"]]
        path = REPO / directory / item["ref"] / filename
        if not path.is_file():
            problems.append(f"{where} does not resolve to a committed {directory}/{item['ref']}/{filename}")
            continue
        source = yaml.safe_load(path.read_text())
        if source.get("id") != item["ref"]:
            problems.append(f"{where} names a source whose id is {source.get('id')!r}")
        if kit not in source["brand"]["kits"]:
            problems.append(f"{where} source is not approved for kit {kit}")
        declared = {variable["key"]: variable for variable in source.get("variables") or []}
        for variant in item["variants"]:
            for key, value in (variant.get("vars") or {}).items():
                if key not in declared:
                    problems.append(f"{where} variant {variant['key']} sets undeclared variable {key}")
                    continue
                problem = _variable_problem(declared[key], value)
                if problem:
                    problems.append(f"{where} variant {variant['key']} var {key} {problem}")

    brief_keys = [entry["brief_key"] for entry in line["launch"]]
    for key in sorted({key for key in brief_keys if brief_keys.count(key) > 1}):
        problems.append(f"launch brief {key} is listed twice")

    for text in _strings(line):
        if URL_RE.search(text):
            problems.append("a line value is a URL")
            break
    return problems


# --- the valid example -------------------------------------------------------


def test_valid_example_validates():
    assert errors(example("product_line.v1.valid.json")) == []


def test_valid_example_resolves_against_the_committed_repo():
    assert line_problems(example("product_line.v1.valid.json")) == []


def test_valid_example_exercises_every_publish_path_and_a_free_item():
    line = example("product_line.v1.valid.json")
    assert {item["publish"] for item in line["items"]} == PUBLISH
    assert any(item["price_idr"] == 0 for item in line["items"])
    assert {item["kind"] for item in line["items"]} >= {"template", "ebook", "course"}
    assert line["launch"]


# --- every committed line ----------------------------------------------------

_LINES = committed_lines()


@pytest.mark.skipif(not _LINES, reason="no product_lines/*.yaml committed yet (TB1/TB6/TB11 add them)")
@pytest.mark.parametrize("path", _LINES, ids=[path.stem for path in _LINES])
def test_committed_line_validates_and_resolves(path):
    line = yaml.safe_load(path.read_text())
    assert errors(line) == [], [error.message for error in errors(line)]
    assert line["id"] == path.stem, f"{path.name}: id {line['id']!r} must equal the file stem"
    assert line_problems(line) == []


@pytest.mark.skipif(not _LINES, reason="no product_lines/*.yaml committed yet (TB1/TB6/TB11 add them)")
@pytest.mark.parametrize("path", _LINES, ids=[path.stem for path in _LINES])
def test_committed_line_header_marks_example_content(path):
    # Track B TB-D9: built-local copy is Claude-authored example content until experts and the
    # founder replace it at the operational gate.
    assert "EXAMPLE CONTENT" in path.read_text().split("\n\n", 1)[0], f"{path.name} header"


# --- refusals ----------------------------------------------------------------


def test_raw_url_fixture_is_refused_on_the_variant_value():
    payload = example("product_line.v1.invalid-raw-url.json")
    assert errors_at(payload, ("items", 0, "variants", 0, "vars")), "a raw URL variant value validated"
    assert "a line value is a URL" in line_problems(payload)


def test_a_url_ref_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["ref"] = "https://cdn.example.test/pack.pdf"
    assert errors_at(payload, ("items", 0, "ref"))


def test_unknown_kind_fixture_is_refused():
    payload = example("product_line.v1.invalid-unknown-kind.json")
    assert errors_at(payload, ("items", 0)), "an unknown item kind validated"
    assert any(error.validator == "enum" for error in errors_at(payload, ("items", 0, "kind")))


def test_negative_price_fixture_is_refused():
    payload = example("product_line.v1.invalid-negative-price.json")
    refused = errors_at(payload, ("items", 0, "price_idr"))
    assert refused and refused[0].validator == "minimum"


def test_a_fractional_price_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["price_idr"] = 1500.5
    assert errors_at(payload, ("items", 0, "price_idr"))


def test_unknown_kit_fixture_is_schema_valid_and_refused_by_resolution():
    # Kits are data (a niche channel kit is a new file, not a schema change), so the schema
    # pins only the code's shape; the committed-kit check is what refuses it.
    payload = example("product_line.v1.invalid-unknown-kit.json")
    assert errors(payload) == []
    assert any("is not a committed brand kit" in problem for problem in line_problems(payload))


def test_an_unknown_top_level_key_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["notes"] = "free-form"
    assert errors_at(payload, ())


def test_an_unknown_item_key_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["url"] = "x"
    assert errors_at(payload, ("items", 0))


def test_an_unknown_launch_key_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["launch"][0]["copy"] = "free text"
    assert errors_at(payload, ("launch", 0))


def test_a_sellable_item_must_name_its_digital_kind():
    payload = example("product_line.v1.valid.json")
    item = next(item for item in payload["items"] if item["publish"] != "none")
    del item["digital_kind"]
    assert errors(payload)


def test_digital_kind_is_a_seeded_code():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["digital_kind"] = "ebook"
    assert errors_at(payload, ("items", 0))


@pytest.mark.parametrize("kind", ["physical", "content_piece"])
def test_source_less_kinds_are_never_published_digitally(kind):
    payload = example("product_line.v1.valid.json")
    payload["items"] = [
        {"kind": kind, "ref": "box-01", "variants": [{"key": "default"}], "price_idr": 0, "publish": "digital",
         "digital_kind": "planner"}
    ]  # fmt: skip
    assert errors_at(payload, ("items", 0))
    payload["items"][0].update(publish="none")
    del payload["items"][0]["digital_kind"]
    assert errors(payload) == []


def test_an_item_needs_at_least_one_variant():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["variants"] = []
    assert errors_at(payload, ("items", 0, "variants"))


def test_a_launch_entry_names_a_committed_f8_platform_and_shape():
    payload = example("product_line.v1.valid.json")
    payload["launch"][0]["platform"] = "myspace"
    assert errors_at(payload, ("launch", 0, "platform"))
    payload = example("product_line.v1.valid.json")
    payload["launch"][0]["shape"] = "reel"
    assert errors_at(payload, ("launch", 0, "shape"))


def test_the_schema_enums_match_the_consumers_vocabularies():
    schema = load(SCHEMA)
    item = schema["properties"]["items"]["items"]["properties"]
    launch = schema["properties"]["launch"]["items"]["properties"]
    assert set(item["kind"]["enum"]) == KINDS
    assert set(item["publish"]["enum"]) == PUBLISH
    assert set(item["digital_kind"]["enum"]) == DIGITAL_KINDS
    assert set(launch["platform"]["enum"]) == PLATFORMS
    assert set(launch["shape"]["enum"]) == SHAPES
    assert set(launch["publish_mode"]["enum"]) == PUBLISH_MODES


def test_undeclared_and_mistyped_variables_are_refused_by_resolution():
    payload = example("product_line.v1.valid.json")
    template = next(item for item in payload["items"] if item["kind"] == "template")
    template["variants"][0]["vars"] = {"not_declared": "x", "age_band": "10-12"}
    problems = line_problems(payload)
    assert any("undeclared variable not_declared" in problem for problem in problems), problems
    assert any("var age_band is not one of" in problem for problem in problems), problems


def test_a_ref_that_does_not_resolve_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["ref"] = "terakidz-does-not-exist"
    assert any("does not resolve" in problem for problem in line_problems(payload))


def test_a_source_not_approved_for_the_kit_is_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"][0]["kit"] = "terafin"
    assert any("not approved for kit terafin" in problem for problem in line_problems(payload))


def test_duplicate_items_and_briefs_are_refused():
    payload = example("product_line.v1.valid.json")
    payload["items"].append(dict(payload["items"][0]))
    payload["launch"].append(dict(payload["launch"][0]))
    problems = line_problems(payload)
    assert any("is listed twice" in problem and "items" in problem for problem in problems), problems
    assert any(problem.startswith("launch brief") for problem in problems), problems


# --- hygiene -----------------------------------------------------------------


def test_no_pii_property_names_in_the_schema():
    names = schema_property_names(load(SCHEMA), registry())
    assert not pii_hits(names), pii_hits(names)


def test_the_schema_is_registered_under_its_contracts_path():
    assert load(SCHEMA)["$id"] == SCHEMA_ID


# Each invalid fixture carries exactly one defect, at this instance path (None: schema-valid,
# refused by resolution). A fixture refused anywhere else fails for the wrong reason.
INVALID_FIXTURES = {
    "product_line.v1.invalid-raw-url.json": ("items", 0, "variants", 0, "vars"),
    "product_line.v1.invalid-unknown-kind.json": ("items", 0),
    "product_line.v1.invalid-negative-price.json": ("items", 0, "price_idr"),
    "product_line.v1.invalid-unknown-kit.json": None,
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
        assert line_problems(payload)
        return
    assert found, f"{name} validated"
    stray = [error for error in found if tuple(error.absolute_path)[: len(prefix)] != prefix]
    assert not stray, [f"{list(error.absolute_path)}: {error.message}" for error in stray]


def test_schema_is_backward_compatible_with_base():
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = SCHEMA.relative_to(REPO).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=REPO, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    assert json.loads(shown.stdout) == load(SCHEMA)
