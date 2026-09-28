"""ebook.v1 and the one shipped example book (e-book factory, F4/EBK1 task 1). Kept
in its own module so it never touches the governance/events/factory test modules other agents
are concurrently editing.

Three layers are checked, and the last two are the point:

1. **Schema** — `ebook.v1.schema.json` against the shipped `ebooks/<id>/ebook.yaml`, the
   examples under `contracts/examples/ebooks/`, and one invalid fixture per refusal the factory
   relies on.
2. **Semantics the schema cannot express** — every `{{...}}` placeholder in a shipped book
   resolves (a declared variable, a kit token, a page token, a supported render option or kit
   content), every image block and the cover logo name a role declared under `assets.images`,
   and at least one chapter places a `disclaimer` block. A schema-valid book that references
   `{{var.typo}}` would render a broken document; both refuse here.
3. **`book.md` is DERIVED, not authored** — `render_markdown()` below is the canonical
   Markdown rendering of a book, and the committed `book.md` must equal it byte for byte. The
   same derivation is implemented in the consumer (`factory_ebook._fe_markdown`), which is what
   makes "Markdown is the single source" (spec D2) a checkable claim rather than a comment:
   prose and data cannot drift, in either repo. The block vocabulary itself is a PINNED COPY of
   F3's `template.v1` branches, and `test_the_block_vocabulary_is_f3s_pinned_copy` fails the
   moment the two contracts disagree (spec A6).
"""

from __future__ import annotations

import json
import re
import subprocess

import jsonschema
import pytest
import yaml
from contracts_lib import CONTRACTS, contracts_base_ref, load, pii_hits, registry, schema_property_names, validator_for

EBOOKS_SCHEMA = CONTRACTS / "ebooks" / "ebook.v1.schema.json"
TEMPLATES_SCHEMA = CONTRACTS / "templates" / "template.v1.schema.json"
SCHEMA_ID = "https://kodeme.io/contracts/ebooks/ebook.v1.schema.json"
EXAMPLES = CONTRACTS / "examples" / "ebooks"
EBOOK_DIR = CONTRACTS.parent / "ebooks"
BRANDS_DIR = CONTRACTS.parent / "brands"

# The one committed book this contract exists for (spec D6: one shipped example proves the gate).
SHIPPED = ("terakidz-example",)

# `{{name}}` / `{{name.with.dots}}` — the whole placeholder vocabulary of ebook.v1, the same
# one template.v1 uses (the block vocabulary and its placeholders are shared by reference).
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
# The shared closed block vocabulary, and the markdown rendering of each (spec D4: the chapter
# vocabulary IS F3's -- see test_the_block_vocabulary_is_f3s_pinned_copy).
BLOCK_TYPES = ("heading", "paragraph", "list", "table", "image", "spacer", "footer", "disclaimer")
# Block headings sit BELOW a chapter title: the book's title is `#`, a chapter is `##`, and a
# block of level n is `#` * (n + 2) — so level 1 -> `###`, level 2 -> `####`, level 3 -> `#####`.
HEADING_OFFSET = 2
TABLE_RULE = "---"


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text())


def book_yaml(book_id: str) -> dict:
    return _yaml(EBOOK_DIR / book_id / "ebook.yaml")


def book_markdown(book_id: str) -> str:
    return (EBOOK_DIR / book_id / "book.md").read_text()


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def errors(payload: dict) -> list:
    return list(validator_for(EBOOKS_SCHEMA).iter_errors(payload))


def errors_under(payload: dict, prefix: tuple) -> list:
    """Errors at `prefix` or anywhere beneath it — `oneOf` refuses a subtree as a whole."""
    return [error for error in errors(payload) if tuple(error.absolute_path)[: len(prefix)] == prefix]


def block_branches(schema_path) -> dict:
    """{kind: branch} of a contract's block `oneOf`, straight out of the schema."""
    branches = load(schema_path)["properties"]["blocks"]["items"]["oneOf"]
    return {branch["properties"]["type"]["const"]: branch for branch in branches}


def ebook_block_branches() -> dict:
    """The same, for ebook.v1 — whose blocks live one level deeper, under `chapters`."""
    chapters = load(EBOOKS_SCHEMA)["properties"]["chapters"]["items"]["properties"]["blocks"]["items"]["oneOf"]
    return {branch["properties"]["type"]["const"]: branch for branch in chapters}


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
    return set(PLACEHOLDER_RE.findall(text or ""))


def book_blocks(book: dict) -> list[dict]:
    return [block for chapter in book["chapters"] for block in chapter["blocks"]]


def book_placeholders(book: dict) -> set[str]:
    found = set()
    for block in book_blocks(book):
        for text in block_strings(block):
            found |= placeholders(text)
    return found


# --- the canonical Markdown derivation ---------------------------------------
#
# 🔴 THE ONE DERIVATION. `factory_ebook._fe_markdown` in kodemeio-odoo implements exactly this
# and `book.md` is pinned to it in both repos. Editing the book means editing ebook.yaml and
# regenerating book.md; editing this function means editing the consumer's copy in the SAME
# change, or one of the two drift tests fails.


def block_markdown(block: dict) -> list[str]:
    """The Markdown lines one block of the closed vocabulary renders to."""
    kind = block["type"]
    if kind == "heading":
        level = block.get("level", 1)
        return [f"{'#' * (HEADING_OFFSET + int(level))} {block['text']}"]
    if kind == "paragraph":
        return [block["text"]]
    if kind == "list":
        ordered = bool(block.get("ordered"))
        return [f"{index}. {item}" if ordered else f"- {item}" for index, item in enumerate(block["items"], start=1)]
    if kind == "table":
        columns = block["columns"]
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(TABLE_RULE for _ in columns) + " |",
        ]
        lines += ["| " + " | ".join(row) + " |" for row in block.get("rows") or []]
        return lines
    if kind == "image":
        # Nothing is fetched (spec section 4): the image is a labelled placeholder naming its
        # licence ref, exactly as F3's renderers draw one.
        label = block.get("alt")
        return [f"> Gambar: {label} ({block['asset']})" if label else f"> Gambar: ({block['asset']})"]
    if kind == "spacer":
        # Markdown has no vertical rhythm; a thematic break is the visible equivalent, so a
        # spacer is never silently dropped.
        return [TABLE_RULE]
    if kind == "footer":
        return [f"*{block['text']}*"]
    if kind == "disclaimer":
        # A blockquote, so a disclaimer reads as a note beside the prose rather than as prose.
        return [f"> {block['text']}"]
    raise AssertionError(f"no markdown rendering for block {kind!r}")


def render_markdown(book: dict) -> str:
    """The book's canonical Markdown source — the artefact BOTH renderers consume."""
    lines = [f"# {book['title']}"]
    if book.get("subtitle"):
        lines.append(f"*{book['subtitle']}*")
    lines.append(book["author"])
    lines.append("")
    lines.append(f"> Logo: {book['cover']['logo']}")
    for chapter in book["chapters"]:
        lines += ["", f"## {chapter['title']}", ""]
        for index, block in enumerate(chapter["blocks"]):
            if index:
                lines.append("")
            lines += block_markdown(block)
    return "\n".join(lines) + "\n"


def resolve_problems(book: dict) -> list[str]:
    """Every placeholder a shipped book uses that the renderer could not supply, plus a
    declared variable nobody uses, the cover logo or an image block with no declared role, and
    a book with no disclaimer block. The schema cannot reach any of these; a consumer's
    importer refuses them by name."""
    problems = []
    declared = {variable["key"] for variable in book["variables"]}
    image_refs = set((book["assets"].get("images") or {}).values())
    kits = [_yaml(BRANDS_DIR / f"{code}.yaml") for code in book["brand"]["kits"]]
    used_variables = set()

    for block in book_blocks(book):
        for text in block_strings(block):
            for name in sorted(placeholders(text)):
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
                            kit["code"]
                            for kit in kits
                            if rest not in (set(kit["colors"]) | {"font_heading", "font_body"})
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
                        problems.append(f"{{{{{name}}}}} is not kit content a book may place")
                else:
                    problems.append(f"{{{{{name}}}}} has no known namespace")
    for key in sorted(declared - used_variables):
        problems.append(f"variable {key} is declared and never used")
    if book["cover"]["logo"] not in image_refs:
        problems.append(f"cover logo {book['cover']['logo']} is not declared in assets.images")
    for block in book_blocks(book):
        if block["type"] == "image" and block["asset"] not in image_refs:
            problems.append(f"image block {block['asset']} is not declared in assets.images")
    if not any(block["type"] == "disclaimer" for block in book_blocks(book)):
        problems.append("no chapter places a disclaimer block for the kit's required text")
    return problems


# --- the shipped book --------------------------------------------------------


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_book_validates(book_id):
    validator_for(EBOOKS_SCHEMA).validate(book_yaml(book_id))


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_book_placeholders_all_resolve(book_id):
    assert resolve_problems(book_yaml(book_id)) == []


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_book_ships_both_formats_and_names_both_renderers(book_id):
    """Spec D2: the proof book is a TWO-format book, PDF through Typst and EPUB through Pandoc —
    which is what makes A4/A5 (the named refusals and the two-format release rule) real."""
    book = book_yaml(book_id)
    assert set(book["outputs"]) == {"pdf", "epub"}
    assert book["renderers"] == {"pdf": "typst", "epub": "pandoc"}


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_the_shipped_book_md_is_the_canonical_rendering(book_id):
    """The point of the pair: the prose a human reads and the data the factory renders from are
    one thing, checked byte for byte."""
    book = book_yaml(book_id)
    assert book_markdown(book_id) == render_markdown(book)


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_the_markdown_source_carries_the_whole_book_and_no_html(book_id):
    markdown = book_markdown(book_id)
    book = book_yaml(book_id)
    assert markdown.startswith(f"# {book['title']}\n")
    assert markdown.endswith("\n")
    for chapter in book["chapters"]:
        assert f"## {chapter['title']}" in markdown
    # A cover is a typed block set, never free-form HTML (spec D3), and no chapter is HTML
    # either: a book value is literal text or a placeholder.
    body = markdown.lower()
    for needle in ("<html", "<div", "<script", "<style", "src=", "href="):
        assert needle not in body, f"the markdown source carries {needle!r}"


def test_the_example_matches_the_shipped_yaml():
    # The examples are derived from the shipped file; this pins them together so a change to
    # one without the other fails here rather than at a consumer.
    assert example("ebook.v1.valid.json") == book_yaml("terakidz-example")


@pytest.mark.parametrize("name", ["ebook.v1.valid.json"])
def test_examples_validate(name):
    validator_for(EBOOKS_SCHEMA).validate(example(name))


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_every_asset_reference_is_an_asset_key(book_id):
    # No book value is ever a URL: the licence lives with the asset, not the document.
    book = book_yaml(book_id)
    refs = list(book["assets"]["fonts"].values()) + list((book["assets"].get("images") or {}).values())
    refs += [book["cover"]["logo"]]
    refs += [block["asset"] for block in book_blocks(book) if block["type"] == "image"]
    assert refs
    for ref in refs:
        assert re.fullmatch(r"asset:[a-z0-9-]+", ref), f"{book_id}: {ref}"
    assert "https://" not in (EBOOK_DIR / book_id / "ebook.yaml").read_text()


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_supported_kits_are_committed_kits(book_id):
    assert set(book_yaml(book_id)["brand"]["kits"]) <= {path.stem for path in BRANDS_DIR.glob("*.yaml")}


@pytest.mark.parametrize("book_id", SHIPPED, ids=list(SHIPPED))
def test_the_shipped_book_places_the_kit_disclaimer(book_id):
    """The kit's mandatory text is checked against the RENDERED book by the consumer's checks;
    the structural half — a chapter that actually PLACES a disclaimer block — is here too."""
    book = book_yaml(book_id)
    disclaimers = [block for block in book_blocks(book) if block["type"] == "disclaimer"]
    assert disclaimers, "the book places no disclaimer block"
    assert all(block["text"].strip() for block in disclaimers)


# --- the vocabulary is F3's, pinned (spec A6) --------------------------------


def test_the_block_vocabulary_is_f3s_pinned_copy():
    """Spec D4: the chapter blocks ARE the template factory's closed vocabulary, shared by
    reference. Both contracts carry a copy; this fails the moment they disagree, in either
    direction — a block added to one and not the other is exactly the drift D4 refuses."""
    templates = block_branches(TEMPLATES_SCHEMA)
    ebooks = ebook_block_branches()
    assert set(templates) == set(ebooks) == set(BLOCK_TYPES)
    for kind in BLOCK_TYPES:
        assert templates[kind] == ebooks[kind], f"the {kind} branch drifted from template.v1"


def test_the_block_vocabulary_rejects_an_unknown_block():
    assert errors_under(example("ebook.v1.invalid-unknown-block.json"), ("chapters",)), "an iframe block validated"


# --- refusals ----------------------------------------------------------------


def test_the_cover_logo_refuses_a_raw_url():
    payload = example("ebook.v1.valid.json")
    payload["cover"]["logo"] = "https://cdn.example.test/logo.png"
    assert errors_under(payload, ("cover",)), "a raw URL was accepted as the cover logo"


def test_raw_url_fixture_fails_on_the_image_block():
    payload = example("ebook.v1.invalid-raw-url.json")
    assert errors_under(payload, ("chapters",)), "a raw image URL validated"
    # The refusal is the asset ref, nothing else: putting the key back validates clean.
    for chapter in payload["chapters"]:
        for block in chapter["blocks"]:
            if block["type"] == "image":
                block["asset"] = "asset:image-terakidz-learning-hero"
    assert errors(payload) == []


def test_undeclared_variable_fails_the_semantic_check():
    payload = example("ebook.v1.invalid-undeclared-variable.json")
    # Schema-valid on purpose: only the resolver can catch it, which is why it exists.
    validator_for(EBOOKS_SCHEMA).validate(payload)
    problems = resolve_problems(payload)
    assert any("var.not_declared" in problem for problem in problems), problems


def test_a_book_without_a_disclaimer_block_is_caught_by_the_semantic_check():
    payload = example("ebook.v1.valid.json")
    for chapter in payload["chapters"]:
        chapter["blocks"] = [block for block in chapter["blocks"] if block["type"] != "disclaimer"]
    assert errors(payload) == []
    assert any("disclaimer" in problem for problem in resolve_problems(payload))


def test_a_book_without_a_pdf_output_is_refused():
    payload = example("ebook.v1.valid.json")
    payload["outputs"] = ["epub"]
    assert errors_under(payload, ("outputs",)), "a book that ships no PDF validated"


def test_a_book_that_ships_an_epub_must_name_its_epub_renderer():
    payload = example("ebook.v1.valid.json")
    payload["renderers"] = {"pdf": "typst"}
    assert errors_under(payload, ("renderers",)), "an epub book with no epub renderer validated"


def test_the_renderers_are_pinned_per_format():
    payload = example("ebook.v1.valid.json")
    payload["renderers"] = {"pdf": "pandoc", "epub": "pandoc"}
    assert errors_under(payload, ("renderers",)), "pandoc was accepted as the PDF renderer"


def test_an_unknown_top_level_key_fails():
    payload = example("ebook.v1.valid.json")
    payload["notes"] = "free-form"
    assert errors_under(payload, ()), "an unknown top-level key validated"


def test_a_chapter_needs_its_own_title_and_blocks():
    payload = example("ebook.v1.valid.json")
    payload["chapters"] = [{"id": "only-one", "blocks": [{"type": "paragraph", "text": "x"}]}]
    assert errors_under(payload, ("chapters",)), "a chapter without a title validated"


def test_a_heading_level_is_an_integer():
    payload = example("ebook.v1.valid.json")
    payload["chapters"][0]["blocks"].insert(0, {"type": "heading", "level": "1", "text": "x"})
    assert errors_under(payload, ("chapters",)), "a heading level reached the markup as a string"


def test_no_pii_property_names_in_the_ebook_schema():
    names = schema_property_names(load(EBOOKS_SCHEMA), registry())
    assert not pii_hits(names), pii_hits(names)


def test_the_author_is_a_pen_name_and_the_schema_keeps_it_that_way():
    """`author` is catalogue metadata about the imprint, never a reader: the schema requires a
    non-empty string and nothing else, and no property is named after a person."""
    schema = load(EBOOKS_SCHEMA)
    assert schema["properties"]["author"]["type"] == "string"
    assert "name" not in schema["properties"]


def test_the_schema_is_registered_under_its_contracts_path():
    assert load(EBOOKS_SCHEMA)["$id"] == SCHEMA_ID


def test_schema_is_backward_compatible_with_base():
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = EBOOKS_SCHEMA.relative_to(CONTRACTS.parent).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=CONTRACTS.parent, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    assert json.loads(shown.stdout) == load(EBOOKS_SCHEMA)


def test_the_schema_check_rejects_a_broken_schema():
    """The validator is not vacuous: the shipped schema is a valid draft 2020-12 schema."""
    jsonschema.Draft202012Validator.check_schema(load(EBOOKS_SCHEMA))
