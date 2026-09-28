"""The Terakon product line B6, content part (Teracorp Track B, slice TB6; spec B6).

`product_lines/terakon.yaml` sells five paid items under the **placeholder** `terakon` kit:

- F3 workbooks: the existing `terakon-planner`, plus `terakon-content-calendar` and the prompt
  pack `terakon-prompt-pack` (digital kind `prompt_pack`);
- the F4 playbook `ebooks/terakon-playbook-channel-niche` (digital kind `book`: there is no
  `playbook` code in `digital.kind`, and K1 pinned the enum);
- the F5 course `courses/terakon-channel-niche-101`.

Membership (MEM, kodemeio-odoo) is a separate slice and closes the B6 row with this one.

The generic contract modules (`test_contracts_{templates,ebooks,courses,product_lines}.py`)
pin their own SHIPPED tuples and must not be edited by later slices, so this module applies the
SAME semantic checks, imported from them rather than copied, to the TB6 sources. On top of them
it proves what the built-local bar needs before the product-line harness (K2) runs the line:

1. every artefact the line renders PLACES the kit's disclaimers, so a render under the draft kit
   carries `kit uji coba` and `factory.check.brand_rules` refuses one that does not (spec 3.1);
2. no TB6 source text trips the kit's forbidden phrases (the same word-boundary,
   case-insensitive match the kernel's `brand_rules` check uses) or makes a guaranteed-outcome
   claim;
3. no source carries a URL, an email address, a phone number or a NIK -- the `links` and `pii`
   checks would refuse them, and the prompt pack is prompt TEXT, never a link list;
4. every workbook block is placed by the sidecar, so no block (least of all the disclaimer) is
   silently absent from the rendered file;
5. the launch briefs name a shape their channel accepts (mirrors the channel rules in
   kodemeio-odoo `factory_content/data/content_channel_rules.yaml`; the harness refuses a
   mismatch by name, this test keeps the committed line from ever carrying one).
"""

from __future__ import annotations

import re

import pytest
import yaml
from contracts_lib import CONTRACTS, validator_for
from test_contracts_courses import COURSES_SCHEMA, course_problems, source_text, sources_of
from test_contracts_ebooks import EBOOKS_SCHEMA, book_blocks, render_markdown
from test_contracts_ebooks import resolve_problems as book_problems
from test_contracts_product_lines import SCHEMA as LINE_SCHEMA
from test_contracts_product_lines import line_problems
from test_contracts_templates import (
    CELL_KEYS,
    CELL_SOURCES,
    FORMULA_RE,
    SHEET_KEYS,
    SHEET_STYLES,
    TEMPLATES_SCHEMA,
    XLSX_BLOCKS,
)
from test_contracts_templates import resolve_problems as template_problems

REPO = CONTRACTS.parent
LINE_PATH = REPO / "product_lines" / "terakon.yaml"
KIT_PATH = REPO / "brands" / "terakon.yaml"

NEW_TEMPLATES = ("terakon-content-calendar", "terakon-prompt-pack")
PLANNER = "terakon-planner"
BOOK = "terakon-playbook-channel-niche"
COURSE = "terakon-channel-niche-101"

# The B6 catalogue (spec B6 "Content line"): (kind, ref) -> (digital_kind, publish).
CATALOGUE = {
    ("template", PLANNER): ("planner", "factory_digital"),
    ("template", "terakon-content-calendar"): ("template_pack", "factory_digital"),
    ("template", "terakon-prompt-pack"): ("prompt_pack", "factory_digital"),
    ("ebook", BOOK): ("book", "digital"),
    ("course", COURSE): ("course", "digital"),
}

# Mirror of `kinds` per channel in kodemeio-odoo factory_content/data/content_channel_rules.yaml
# (read 2026-09-28). No channel has a thread form; YouTube has no carousel.
CHANNEL_KINDS = {
    "tiktok": {"caption", "script", "carousel"},
    "youtube": {"caption", "script"},
    "instagram": {"caption", "script", "carousel"},
    "facebook": {"caption", "carousel"},
}

# Outcome claims the example copy never makes. The draft kit forbids only two phrases, but a
# course that sells "how to run a niche channel" is exactly where a guaranteed-growth or
# guaranteed-income line would creep in, so the TB6 copy is held to this list as well. A bare
# "dijamin" is not listed: honest copy says "tidak ada jaminan".
CLAIM_PHRASES = (
    "dijamin viral",
    "dijamin untung",
    "dijamin laku",
    "pasti viral",
    "pasti untung",
    "pasti cuan",
    "penghasilan pasti",
    "cepat kaya",
    "passive income",
)

URL_RE = re.compile(r"(https?:|ftp:|://|\bwww\.|\bmailto:|\btel:|\bjavascript:)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?62|0)8\d{7,12}(?!\d)")
NIK_RE = re.compile(r"(?<!\d)\d{16}(?!\d)")


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text())


def line() -> dict:
    return _yaml(LINE_PATH)


def kit() -> dict:
    return _yaml(KIT_PATH)


def template_yaml(template_id: str) -> dict:
    return _yaml(REPO / "templates" / template_id / "template.yaml")


def template_layout(template: dict) -> str:
    return (REPO / "templates" / template["id"] / template["layout"]).read_text()


def book_yaml() -> dict:
    return _yaml(REPO / "ebooks" / BOOK / "ebook.yaml")


def course_yaml() -> dict:
    return _yaml(REPO / "courses" / COURSE / "course.yaml")


def tb6_files() -> list:
    """Every file this slice authored (the planner is K0/F3's and is not re-checked here)."""
    directories = [REPO / "templates" / template_id for template_id in NEW_TEMPLATES]
    directories += [REPO / "ebooks" / BOOK, REPO / "courses" / COURSE]
    files = [LINE_PATH] if LINE_PATH.is_file() else []
    for directory in directories:
        files += sorted(directory.iterdir()) if directory.is_dir() else []
    return files


def sold_files() -> list:
    """Every source file the line renders (TB6's own plus the pre-existing planner)."""
    planner = sorted((REPO / "templates" / PLANNER).iterdir())
    return [path for path in tb6_files() if path != LINE_PATH] + planner


def _phrase_re(phrase: str):
    """`factory.check.brand_rules._fc_phrase_re`: word-bounded, whitespace-tolerant, any case."""
    words = [re.escape(word) for word in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


# --- the line ----------------------------------------------------------------


def test_the_line_validates_and_resolves_against_the_committed_repo():
    payload = line()
    assert list(validator_for(LINE_SCHEMA).iter_errors(payload)) == []
    assert payload["id"] == LINE_PATH.stem
    assert line_problems(payload) == []


def test_the_line_renders_under_the_placeholder_terakon_kit_only():
    payload = line()
    assert payload["brand_kit"] == "terakon"
    assert all(item.get("kit", "terakon") == "terakon" for item in payload["items"])
    assert kit()["status"] == "draft"
    assert any("kit uji coba" in text for text in kit()["rules"]["required_disclaimers"])


def test_the_line_sells_exactly_the_b6_catalogue_all_paid():
    items = {(item["kind"], item["ref"]): item for item in line()["items"]}
    assert set(items) == set(CATALOGUE)
    for key, (digital_kind, publish) in CATALOGUE.items():
        assert items[key]["digital_kind"] == digital_kind, key
        assert items[key]["publish"] == publish, key
        assert items[key]["price_idr"] > 0, f"{key} is a paid item in B6"


def test_every_workbook_item_has_two_variants_that_differ_in_their_inputs():
    # Two variants of one template must be two distinct renders (distinct input_sha256 at L2),
    # so their vars differ; a variant pair that sets the same values is one render twice.
    for item in line()["items"]:
        if item["kind"] != "template":
            continue
        assert len(item["variants"]) >= 2, item["ref"]
        rendered = [tuple(sorted((variant.get("vars") or {}).items())) for variant in item["variants"]]
        assert len(set(rendered)) == len(rendered), item["ref"]


def test_three_launch_briefs_each_on_a_channel_that_accepts_its_shape():
    launch = line()["launch"]
    assert len(launch) == 3
    for entry in launch:
        assert entry["shape"] in CHANNEL_KINDS[entry["platform"]], entry
    # Nothing publishes directly from a placeholder kit: the briefs hand off to a creator.
    assert all(entry.get("publish_mode", "creator_handoff") == "creator_handoff" for entry in launch)


# --- the two new workbooks ---------------------------------------------------


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_new_workbook_validates_and_every_placeholder_resolves(template_id):
    template = template_yaml(template_id)
    assert list(validator_for(TEMPLATES_SCHEMA).iter_errors(template)) == []
    assert template["id"] == template_id
    assert template["renderer"] == "xlsx"
    assert template_problems(template, template_layout(template)) == []


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_new_workbook_sidecar_is_data_in_the_closed_vocabulary(template_id):
    template = template_yaml(template_id)
    sidecar = yaml.safe_load(template_layout(template))
    declared = {variable["key"] for variable in template["variables"]}
    assert set(sidecar) == {"sheets"} and sidecar["sheets"]
    assert {block["type"] for block in template["blocks"]} <= XLSX_BLOCKS
    for sheet in sidecar["sheets"]:
        assert set(sheet) <= SHEET_KEYS, sheet
        assert sheet["name"] and sheet["cells"]
        for cell in sheet["cells"]:
            assert set(cell) <= CELL_KEYS, cell
            assert sum(source in cell for source in CELL_SOURCES) == 1, cell
            if "var" in cell:
                assert cell["var"] in declared, cell
            if "block" in cell:
                assert 0 <= cell["block"] < len(template["blocks"]), cell
            if "formula" in cell:
                assert FORMULA_RE.match(cell["formula"]), cell["formula"]
            if "style" in cell:
                assert cell["style"] in SHEET_STYLES, cell["style"]


@pytest.mark.parametrize("template_id", (*NEW_TEMPLATES, PLANNER))
def test_every_workbook_block_is_placed_and_the_disclaimer_is_among_them(template_id):
    template = template_yaml(template_id)
    sidecar = yaml.safe_load(template_layout(template))
    placed = {cell["block"] for sheet in sidecar["sheets"] for cell in sheet["cells"] if "block" in cell}
    assert placed == set(range(len(template["blocks"]))), f"{template_id}: unplaced blocks"
    disclaimers = [
        index
        for index, block in enumerate(template["blocks"])
        if block["type"] == "disclaimer" and "{{kit.disclaimers}}" in block["text"]
    ]
    assert disclaimers and set(disclaimers) <= placed


def test_the_prompt_pack_is_a_table_of_prompt_text():
    template = template_yaml("terakon-prompt-pack")
    tables = [block for block in template["blocks"] if block["type"] == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert "Prompt" in table["columns"]
    column = table["columns"].index("Prompt")
    prompts = [row[column] for row in table["rows"]]
    assert len(prompts) >= 8
    assert all(len(row) == len(table["columns"]) for row in table["rows"])
    assert all(len(prompt) >= 40 for prompt in prompts), "a prompt is a full instruction"
    assert len(set(prompts)) == len(prompts)


# --- the playbook e-book -----------------------------------------------------


def test_the_playbook_validates_and_resolves():
    book = book_yaml()
    assert list(validator_for(EBOOKS_SCHEMA).iter_errors(book)) == []
    assert book["id"] == BOOK
    assert book_problems(book) == []


def test_the_playbook_book_md_is_the_canonical_rendering():
    assert (REPO / "ebooks" / BOOK / "book.md").read_text() == render_markdown(book_yaml())


def test_the_playbook_places_the_disclaimer_first_and_last():
    chapters = book_yaml()["chapters"]
    for chapter in (chapters[0], chapters[-1]):
        texts = [block.get("text", "") for block in chapter["blocks"] if block["type"] == "disclaimer"]
        assert "{{kit.disclaimers}}" in texts, chapter["id"]


def test_the_playbook_ships_both_formats():
    book = book_yaml()
    assert book["outputs"] == ["pdf", "epub"]
    assert book["renderers"] == {"pdf": "typst", "epub": "pandoc"}


def test_the_playbook_cover_uses_a_terakon_asset_not_a_terakidz_one():
    book = book_yaml()
    refs = [book["cover"]["logo"], *book["assets"]["fonts"].values(), *(book["assets"].get("images") or {}).values()]
    refs += [block["asset"] for block in book_blocks(book) if block["type"] == "image"]
    assert all("terakidz" not in ref for ref in refs), refs
    assert set(book["assets"]["fonts"].values()) <= set(kit()["assets"])


# --- the course --------------------------------------------------------------


def test_the_course_validates_and_has_no_unresolvable_problem():
    course = course_yaml()
    assert list(validator_for(COURSES_SCHEMA).iter_errors(course)) == []
    assert course["id"] == COURSE
    assert course_problems(course) == []


def test_every_lesson_reading_and_deck_places_the_kit_disclaimers():
    course = course_yaml()
    for _lesson, reading, slides in sources_of(course):
        assert "{{kit.disclaimers}}" in source_text(course, reading), reading
        assert "{{kit.disclaimers}}" in source_text(course, slides), slides


def test_the_course_names_no_video_until_f6_is_built_and_carries_a_quiz():
    # A lesson video refuses the render by name until a licensed footage asset exists (F6), and
    # the B6 line must render end to end on built-local, so this course carries none.
    course = course_yaml()
    lessons = [lesson for module in course["modules"] for lesson in module["lessons"]]
    assert not any(lesson.get("video") for lesson in lessons)
    assert not (course["assets"].get("footage") or {})
    assert sum(1 for lesson in lessons if lesson.get("quiz")) >= 2
    assert len(course["modules"]) == 3


# --- every TB6 source --------------------------------------------------------


@pytest.mark.parametrize("path", tb6_files(), ids=lambda path: path.relative_to(REPO).as_posix())
def test_every_authored_yaml_header_marks_example_content(path):
    if path.suffix != ".yaml":
        pytest.skip("Markdown sources carry no comment syntax; their course.yaml / ebook.yaml header covers them")
    assert "EXAMPLE CONTENT" in path.read_text().split("\n\n", 1)[0], path.name


def test_every_tb6_source_is_approved_for_terakon_only():
    for template_id in NEW_TEMPLATES:
        assert template_yaml(template_id)["brand"]["kits"] == ["terakon"]
    assert book_yaml()["brand"]["kits"] == ["terakon"]
    assert course_yaml()["brand"]["kits"] == ["terakon"]


@pytest.mark.parametrize("path", sold_files(), ids=lambda path: path.relative_to(REPO).as_posix())
def test_no_source_trips_a_kit_forbidden_phrase_or_a_claim(path):
    text = path.read_text()
    for phrase in [*kit()["rules"]["forbidden_phrases"], *CLAIM_PHRASES]:
        assert not _phrase_re(phrase).search(text), f"{path.name} carries a refused phrase"


@pytest.mark.parametrize("path", tb6_files(), ids=lambda path: path.relative_to(REPO).as_posix())
def test_no_source_carries_a_url_or_personal_data(path):
    # Checked on the text a reader or a check sees: whole-line YAML comments are dropped so a
    # header's prose ABOUT urls is not itself a finding. Markdown has no comments.
    lines = path.read_text().splitlines()
    if path.suffix == ".yaml":
        lines = [line for line in lines if not line.lstrip().startswith("#")]
    text = "\n".join(lines)
    assert not URL_RE.search(text), f"{path.name}: URL"
    assert not EMAIL_RE.search(text), f"{path.name}: email"
    assert not PHONE_RE.search(text), f"{path.name}: phone"
    assert not NIK_RE.search(text), f"{path.name}: NIK"


def test_the_checks_catch_what_they_claim_to():
    # The local mirrors are only worth something if they fire: pin each on a known-bad string.
    assert _phrase_re("bebas risiko").search("Strategi ini BEBAS\nRISIKO.")
    assert not _phrase_re("pasti viral").search("tidak pasti viralnya")
    assert URL_RE.search("lihat www.contoh.test")
    assert EMAIL_RE.search("kirim ke admin@contoh.test")
    assert PHONE_RE.search("hubungi 081234567890")
    assert NIK_RE.search("3201234567890123")
