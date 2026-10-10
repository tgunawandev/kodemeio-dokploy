"""template.v1 and the two shipped example templates (template factory, F3/TPL1
task 1). Kept in its own module so it never touches the governance/events/factory test
modules other agents are concurrently editing.

Two layers are checked, and the second one is the point:

1. **Schema** -- `template.v1.schema.json` against the shipped `templates/<id>/template.yaml`
   files, the examples under `contracts/examples/templates/`, and one invalid fixture per
   refusal the factory relies on.
2. **Semantics the schema cannot express** -- every `{{...}}` placeholder in a shipped
   template resolves (a declared variable, a kit token every supported kit really carries, a
   page token, a supported render option, or `{{blocks}}` in a layout), and every image block
   names an image role declared under `assets.images`. A schema-valid template that references
   `{{var.typo}}` or an undeclared image would render a broken document; both refuse here.
"""

from __future__ import annotations

import json
import re
import subprocess

import jsonschema
import pytest
import yaml
from contracts_lib import CONTRACTS, contracts_base_ref, load, pii_hits, registry, schema_property_names, validator_for

TEMPLATES_SCHEMA = CONTRACTS / "templates" / "template.v1.schema.json"
SCHEMA_ID = "https://kodeme.io/contracts/templates/template.v1.schema.json"
EXAMPLES = CONTRACTS / "examples" / "templates"
TEMPLATE_DIR = CONTRACTS.parent / "templates"
BRANDS_DIR = CONTRACTS.parent / "brands"

# The two committed templates this contract exists for (spec D10: one PDF, one workbook).
SHIPPED = ("terakidz-learning-pack", "terakona-planner")

# `{{name}}` / `{{name.with.dots}}` -- the whole placeholder vocabulary of template.v1.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")

# Keys of `factory.brand.kit._fb_theme()`: the 13 colour/radius tokens of brand_kit.v1's
# `colors` plus the two font families resolved from verified font assets.
THEME_TOKENS = frozenset(
    {
        "primary",
        "primary_foreground",
        "accent",
        "accent_foreground",
        "background",
        "foreground",
        "muted",
        "muted_foreground",
        "card",
        "card_foreground",
        "border",
        "ring",
        "radius",
        "font_heading",
        "font_body",
    }
)
PAGE_TOKENS = frozenset({"size", "orientation", "margin_top", "margin_right", "margin_bottom", "margin_left"})
OPTION_NAMES = frozenset({"locale", "palette", "page_size", "orientation"})
KIT_TOKENS = frozenset({"disclaimers", "ai_disclosure"})
XLSX_BLOCKS = frozenset({"heading", "paragraph", "list", "table", "footer", "disclaimer"})
# The workbook sidecar's closed vocabularies (spec D2 amendment: data, never code).
CELL_SOURCES = ("text", "var", "block", "formula")
CELL_KEYS = frozenset({"ref", "text", "var", "block", "formula", "type", "style"})
SHEET_KEYS = frozenset({"name", "columns", "rows", "freeze", "cells"})
SHEET_STYLES = frozenset({"title", "note", "header", "body", "grid", "footer", "notice"})
FORMULA_RE = re.compile(r"^(SUM|AVG|MIN|MAX|COUNT)\([A-Z]{1,3}[0-9]{1,7}(:[A-Z]{1,3}[0-9]{1,7})?\)$")
# Markup a filled HTML sidecar must never carry: the renderer builds the document's markup,
# the layout only places it (and wkhtmltopdf runs with javascript and local files disabled).
FORBIDDEN_LAYOUT_MARKUP = ("<script", "<iframe", "<object", "<embed", "javascript:", " onload", " onerror")


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text())


def template_yaml(template_id: str) -> dict:
    return _yaml(TEMPLATE_DIR / template_id / "template.yaml")


def layout_source(template: dict) -> str:
    return (TEMPLATE_DIR / template["id"] / template["layout"]).read_text()


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def errors(payload: dict) -> list:
    return list(validator_for(TEMPLATES_SCHEMA).iter_errors(payload))


def block_branch(kind: str) -> dict:
    """One branch of the closed block vocabulary, straight out of the schema."""
    branches = load(TEMPLATES_SCHEMA)["properties"]["blocks"]["items"]["oneOf"]
    return next(branch for branch in branches if branch["properties"]["type"]["const"] == kind)


def errors_under(payload: dict, prefix: tuple) -> list:
    """Errors at `prefix` or anywhere beneath it -- `oneOf` refuses a subtree as a whole."""
    return [error for error in errors(payload) if tuple(error.absolute_path)[: len(prefix)] == prefix]


def block_strings(block: dict) -> list[str]:
    """Every string a reader sees in one block (text, list items, table cells, alt)."""
    strings = []
    for key in ("text", "alt"):
        value = block.get(key)
        if isinstance(value, str):
            strings.append(value)
    for key in ("items", "columns", "rows"):
        value = block.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    strings.append(item)
                elif isinstance(item, list):
                    strings.extend(sub for sub in item if isinstance(sub, str))
    return strings


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def template_placeholders(template: dict) -> set[str]:
    found = set()
    for block in template["blocks"]:
        for text in block_strings(block):
            found |= placeholders(text)
    return found


def resolve_problems(template: dict, layout: str) -> list[str]:
    """Every placeholder a shipped template uses that the renderer could not supply, plus a
    declared variable nobody uses and an image block with no declared role. The schema cannot
    reach any of these; a consumer's importer refuses them by name."""
    problems = []
    declared = {variable["key"] for variable in template["variables"]}
    image_refs = set((template["assets"].get("images") or {}).values())
    kits = [_yaml(BRANDS_DIR / f"{code}.yaml") for code in template["brand"]["kits"]]
    used_variables = set()

    scopes = [("block", text) for block in template["blocks"] for text in block_strings(block)]
    scopes.append(("layout", layout))
    for scope, text in scopes:
        for name in sorted(placeholders(text)):
            if name == "blocks":
                if scope != "layout":
                    problems.append("{{blocks}} only appears in a layout source")
                continue
            head, _, rest = name.partition(".")
            if head == "var":
                used_variables.add(rest)
                if rest not in declared:
                    problems.append(f"{{{{{name}}}}} is not a declared variable")
            elif head == "theme":
                if rest not in THEME_TOKENS:
                    problems.append(f"{{{{{name}}}}} is not a theme token")
                else:
                    missing = [
                        kit["code"] for kit in kits if rest not in (set(kit["colors"]) | {"font_heading", "font_body"})
                    ]
                    if missing:
                        problems.append(f"{{{{{name}}}}} is not carried by kit(s) {', '.join(missing)}")
            elif head == "page":
                if rest not in PAGE_TOKENS:
                    problems.append(f"{{{{{name}}}}} is not a page token")
            elif head == "option":
                if rest not in OPTION_NAMES:
                    problems.append(f"{{{{{name}}}}} is not a render option")
            elif head == "kit":
                if rest not in KIT_TOKENS:
                    problems.append(f"{{{{{name}}}}} is not kit content a template may place")
            else:
                problems.append(f"{{{{{name}}}}} has no known namespace")

    for key in sorted(declared - used_variables):
        problems.append(f"variable {key} is declared and never used")
    for block in template["blocks"]:
        if block["type"] == "image" and block["asset"] not in image_refs:
            problems.append(f"image block {block['asset']} is not declared in assets.images")
    return problems


# --- the shipped templates ---------------------------------------------------


@pytest.mark.parametrize("template_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_template_validates(template_id):
    validator_for(TEMPLATES_SCHEMA).validate(template_yaml(template_id))


@pytest.mark.parametrize("template_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_template_placeholders_all_resolve(template_id):
    template = template_yaml(template_id)
    assert resolve_problems(template, layout_source(template)) == []


def test_shipped_templates_are_one_pdf_and_one_workbook():
    assert {template_yaml(template_id)["kind"] for template_id in SHIPPED} == {"pdf", "xlsx"}


def test_examples_match_the_shipped_yaml():
    # The examples are derived from the shipped files; this pins them together so a change to
    # one without the other fails here rather than at a consumer.
    assert example("template.v1.valid.json") == template_yaml("terakidz-learning-pack")
    assert example("template.v1.xlsx.json") == template_yaml("terakona-planner")


@pytest.mark.parametrize("name", ["template.v1.valid.json", "template.v1.xlsx.json"])
def test_examples_validate(name):
    validator_for(TEMPLATES_SCHEMA).validate(example(name))


@pytest.mark.parametrize("template_id", SHIPPED, ids=list(SHIPPED))
def test_every_asset_reference_is_an_asset_key(template_id):
    # No template value is ever a URL: the licence lives with the asset, not the document.
    template = template_yaml(template_id)
    refs = list(template["assets"]["fonts"].values()) + list((template["assets"].get("images") or {}).values())
    refs += [block["asset"] for block in template["blocks"] if block["type"] == "image"]
    assert refs
    for ref in refs:
        assert re.fullmatch(r"asset:[a-z0-9-]+", ref), f"{template_id}: {ref}"
    assert "https://" not in (TEMPLATE_DIR / template_id / "template.yaml").read_text()


@pytest.mark.parametrize("template_id", SHIPPED, ids=list(SHIPPED))
def test_supported_kits_are_committed_kits(template_id):
    assert set(template_yaml(template_id)["brand"]["kits"]) <= {path.stem for path in BRANDS_DIR.glob("*.yaml")}


def test_the_workbook_sidecar_is_data_in_a_closed_vocabulary():
    """The xlsx sidecar is interpreted, never executed (spec D2 amendment). Every key it may
    carry, every content source, every style and every formula name is one of a fixed set."""
    template = template_yaml("terakona-planner")
    sidecar = _yaml(TEMPLATE_DIR / template["id"] / template["layout"])
    declared = {variable["key"] for variable in template["variables"]}
    blocks = template["blocks"]
    assert set(sidecar) == {"sheets"}
    assert sidecar["sheets"]
    for sheet in sidecar["sheets"]:
        assert set(sheet) <= SHEET_KEYS, sheet
        assert sheet["name"] and sheet["cells"]
        for cell in sheet["cells"]:
            assert set(cell) <= CELL_KEYS, cell
            assert sum(source in cell for source in CELL_SOURCES) == 1, cell
            assert cell["ref"]
            if "var" in cell:
                assert cell["var"] in declared, cell
            if "block" in cell:
                assert 0 <= cell["block"] < len(blocks), cell
            if "formula" in cell:
                assert FORMULA_RE.match(cell["formula"]), cell["formula"]
            if "style" in cell:
                assert cell["style"] in SHEET_STYLES, cell["style"]


@pytest.mark.parametrize("template_id", SHIPPED, ids=list(SHIPPED))
def test_the_layout_sidecar_carries_no_markup_of_its_own(template_id):
    template = template_yaml(template_id)
    if not template["layout"].endswith(".html"):
        pytest.skip(f"{template_id} is not filled into HTML")
    body = layout_source(template).lower()
    for needle in FORBIDDEN_LAYOUT_MARKUP:
        assert needle not in body, f"{template_id} layout carries {needle!r}"


# --- refusals ----------------------------------------------------------------


def test_the_image_block_refuses_a_raw_url():
    schema = block_branch("image")
    jsonschema.Draft202012Validator(schema).validate({"type": "image", "asset": "asset:font-nunito"})
    with pytest.raises(jsonschema.ValidationError) as refused:
        jsonschema.Draft202012Validator(schema).validate({"type": "image", "asset": "https://cdn.example.test/x.png"})
    assert refused.value.validator == "pattern"
    assert tuple(refused.value.absolute_path) == ("asset",)


def test_raw_url_fixture_fails_on_the_image_block():
    payload = example("template.v1.invalid-raw-url.json")
    assert errors_under(payload, ("blocks",)), "a raw image URL validated"
    # The refusal is the asset ref, nothing else: putting the key back validates clean.
    for block in payload["blocks"]:
        if block["type"] == "image":
            block["asset"] = "asset:image-terakidz-learning-hero"
    assert errors(payload) == []


def test_undeclared_variable_fails_the_semantic_check():
    payload = example("template.v1.invalid-undeclared-variable.json")
    # Schema-valid on purpose: only the resolver can catch it, which is why it exists.
    validator_for(TEMPLATES_SCHEMA).validate(payload)
    problems = resolve_problems(payload, layout_source(template_yaml("terakidz-learning-pack")))
    assert any("var.not_declared" in problem for problem in problems), problems


def test_closed_block_vocabulary_rejects_an_unknown_block():
    assert errors_under(example("template.v1.invalid-unknown-block.json"), ("blocks",)), "an iframe block validated"


def test_fonts_live_in_the_fonts_bucket_and_nowhere_else():
    payload = example("template.v1.invalid-font-bucket.json")
    assert errors_under(payload, ("assets",)), "assets.font was accepted as a font bucket"
    assert set(template_yaml("terakidz-learning-pack")["assets"]) == {"fonts", "images"}


def test_a_font_ref_that_is_not_an_asset_fails():
    payload = example("template.v1.invalid-font-not-an-asset.json")
    assert errors_under(payload, ("assets", "fonts")), "a URL was accepted as a font ref"


def test_the_renderer_pins_the_layout_extension():
    assert errors_under(example("template.v1.invalid-wrong-layout-extension.json"), ("layout",)), (
        "a pdf template accepted a .py layout"
    )
    assert errors_under(example("template.v1.invalid-python-layout.json"), ("layout",)), (
        "an xlsx template accepted a .py layout"
    )


def test_a_layout_is_never_a_program():
    # Spec D2 amendment: the sidecar is data. The contract refuses `.py` for BOTH renderers,
    # so a merged template can never be a file a renderer executes.
    schema = load(TEMPLATES_SCHEMA)
    assert ".py" not in schema["properties"]["layout"]["pattern"]
    for template_id in SHIPPED:
        template = template_yaml(template_id)
        suffix = template["layout"].rsplit(".", 1)[-1]
        assert suffix in {"html", "yaml", "json"}, template["layout"]


def test_a_heading_level_is_an_integer():
    assert errors_under(example("template.v1.invalid-hostile-heading-level.json"), ("blocks",)), (
        "a heading level reached the markup as a string"
    )
    for template_id in SHIPPED:
        for block in template_yaml(template_id)["blocks"]:
            if "level" in block:
                assert block["level"] in (1, 2, 3)


def test_an_xlsx_template_refuses_image_and_spacer_blocks():
    payload = example("template.v1.invalid-xlsx-image-block.json")
    assert errors_under(payload, ("blocks",)), "an image block validated in an xlsx template"
    for block in template_yaml("terakona-planner")["blocks"]:
        assert block["type"] in XLSX_BLOCKS


def test_an_unknown_top_level_key_fails():
    payload = example("template.v1.valid.json")
    payload["notes"] = "free-form"
    assert errors_under(payload, ()), "an unknown top-level key validated"


def test_no_pii_property_names_in_the_template_schema():
    names = schema_property_names(load(TEMPLATES_SCHEMA), registry())
    assert not pii_hits(names), pii_hits(names)


def test_the_schema_is_registered_under_its_contracts_path():
    assert load(TEMPLATES_SCHEMA)["$id"] == SCHEMA_ID


def test_schema_is_backward_compatible_with_base():
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = TEMPLATES_SCHEMA.relative_to(CONTRACTS.parent).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=CONTRACTS.parent, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    assert json.loads(shown.stdout) == load(TEMPLATES_SCHEMA)
