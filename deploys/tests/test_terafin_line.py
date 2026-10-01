"""Terafina product line B11 (Track B, slice TB11): financial EDUCATION content under the
placeholder `terafin` kit -- and strictly no financial advice.

What this module proves, on the dokploy side (the Odoo e2e is the K2 harness; see the runbook
`ops/runbooks/terafin-b11-line.md` for exactly what K2 must run):

1. **The four sources are valid factory inputs.** Each one is checked with the SAME helpers the
   F3/F4/F5 contract suites use for their shipped proofs (imported, never re-implemented): the
   schema, every placeholder resolves, `book.md` is the canonical derivation of `ebook.yaml`,
   the course's Markdown vocabulary and quiz shape (every question carries an explanation).
2. **Every artefact carries the no-advice disclaimer.** Each source places `{{kit.disclaimers}}`
   wherever the kernel check looks (every template, the first and last chapter of the book, every
   course reading AND every slide deck), and the terafin kit's required disclaimers include the
   education line.
3. **The copy is education only.** No kit forbidden phrase, no instrument name, no return
   promise and no URL anywhere a reader can see -- for every variant the line renders.
4. **The advice refusal.** A copy of each of the template, the e-book, the course and an F8 piece
   with `rekomendasi saham` injected is refused by `brand_rules`, naming the check code and the
   kit's phrase, never echoing the injected copy. This runs against a PINNED COPY of the kernel
   check (`FactoryCheckBrandRules` in kodemeio-odoo
   `src/private/factory/factory_base/models/factory_check.py`: word-boundary, case-insensitive,
   whitespace-tolerant phrase match; whitespace-normalised, casefolded disclaimer containment).
   It is a pre-proof, not the proof: the K2 harness repeats it on the real kernel.
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml
from contracts_lib import CONTRACTS, validator_for
from test_contracts_courses import (
    COURSES_SCHEMA,
    course_problems,
    sources_of,
)
from test_contracts_courses import (
    source_text as course_source_text,
)
from test_contracts_ebooks import EBOOKS_SCHEMA, render_markdown
from test_contracts_ebooks import resolve_problems as book_resolve_problems
from test_contracts_product_lines import line_problems
from test_contracts_templates import (
    CELL_KEYS,
    CELL_SOURCES,
    FORBIDDEN_LAYOUT_MARKUP,
    FORMULA_RE,
    SHEET_KEYS,
    SHEET_STYLES,
    TEMPLATES_SCHEMA,
    block_strings,
)
from test_contracts_templates import resolve_problems as template_resolve_problems

REPO = CONTRACTS.parent
KIT_PATH = REPO / "brands" / "terafin.yaml"
LINE_PATH = REPO / "product_lines" / "terafin.yaml"
RUNBOOK = REPO / "ops" / "runbooks" / "terafin-b11-line.md"

BUDGET_PLANNER = "terafin-budget-planner"
CASHFLOW_WORKSHEET = "terafin-cashflow-worksheet"
TEMPLATES = (BUDGET_PLANNER, CASHFLOW_WORKSHEET)
EBOOK = "terafin-dasar-keuangan-keluarga"
COURSE = "terafin-literasi-keuangan-101"

EDUCATION_DISCLAIMER = "Konten edukasi, bukan nasihat keuangan atau ajakan berinvestasi."
ADVICE_PHRASE = "rekomendasi saham"
EXAMPLE_MARKER = "EXAMPLE CONTENT"

# TB11's authoring lint, STRICTER than the kit (the kit forbids claim-shaped phrases; this also
# keeps instrument names and return vocabulary out of education copy, per the plan: "no instrument
# names, no buy/sell calls and no return promises"). Word-boundary, case-insensitive. It is a
# content rule for these sources, not a kernel rule: the founder may relax it with the real kit.
INSTRUMENT_WORDS = (
    "saham",
    "reksa dana",
    "reksadana",
    "obligasi",
    "sukuk",
    "sbn",
    "deposito",
    "kripto",
    "crypto",
    "bitcoin",
    "emas",
    "forex",
    "valas",
    "trading",
    "p2p",
    "pinjol",
    "asuransi",
    "emiten",
    "portofolio",
)
RETURN_WORDS = ("cuan", "profit", "imbal hasil", "return", "untung", "keuntungan", "bunga majemuk")
CALL_WORDS = ("beli", "jual", "belilah", "juallah", "segera investasikan")

URL_RE = re.compile(r"(https?://|://|\bwww\.)", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")

# F8 channel -> the piece kinds it accepts (pinned copy of kodemeio-odoo
# factory_content/data/content_channel_rules.yaml `kinds`). K1 left platform/shape validity to
# the harness; TB11 pins its own three briefs here so a bad pair fails before K2 ever runs.
CHANNEL_KINDS = {
    "tiktok": {"caption", "script", "carousel"},
    "youtube": {"caption", "script"},
    "instagram": {"caption", "script", "carousel"},
    "facebook": {"caption", "script", "carousel"},
}
# The channel-level AI disclosure every F8 piece carries (same file, `required_disclosure`).
CHANNEL_AI_DISCLOSURE = "Konten ini dibuat dengan bantuan kecerdasan buatan (AI)."


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text())


def kit() -> dict:
    return _yaml(KIT_PATH)


def line() -> dict:
    return _yaml(LINE_PATH)


def template(template_id: str) -> dict:
    return _yaml(REPO / "templates" / template_id / "template.yaml")


def book() -> dict:
    return _yaml(REPO / "ebooks" / EBOOK / "ebook.yaml")


def course() -> dict:
    return _yaml(REPO / "courses" / COURSE / "course.yaml")


# --- a pinned copy of the kernel's brand_rules check ------------------------------------------


def _normalise_text(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _phrase_re(phrase: str) -> re.Pattern:
    words = [re.escape(word) for word in str(phrase).split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def brand_rules(texts: list[str], rules: dict) -> list[tuple[str, bool, str]]:
    """(code, passed, message) rows, exactly as `FactoryCheckBrandRules._fc_run` builds them."""
    results = []
    for phrase in rules.get("forbidden_phrases") or []:
        if not str(phrase).split():
            continue
        pattern = _phrase_re(phrase)
        for index, text in enumerate(texts):
            match = pattern.search(text)
            if match:
                results.append(("brand_rules", False, f"texts[{index}]@{match.start()}: forbidden phrase {phrase!r}"))
    body = _normalise_text(" ".join(texts))
    for number, disclaimer in enumerate(rules.get("required_disclaimers") or []):
        if _normalise_text(disclaimer) not in body:
            results.append(("brand_rules", False, f"required disclaimer #{number + 1} missing"))
    return results or [("brand_rules", True, "brand rules met")]


def content_brand_rules(
    copy_texts: list[str], rules: dict, *, ai_generated: bool = True
) -> list[tuple[str, bool, str]]:
    """The F8 half: a pinned copy of the phrase/disclaimer/AI-disclosure part of kodemeio-odoo
    `factory_content/models/factory_check_content.py` `content_brand_rules`. A generated piece
    must ALSO carry the kit's own `rules.ai_disclosure`, on top of the kit disclaimers and the
    channel's disclosure (which `content_platform_rules` checks). The kit-supersession half needs
    the ORM and is K2's to prove."""
    results = []
    for phrase in rules.get("forbidden_phrases") or []:
        if not str(phrase or "").split():
            continue
        pattern = _phrase_re(phrase)
        for index, text in enumerate(copy_texts):
            match = pattern.search(text)
            if match:
                results.append(
                    ("content_brand_rules", False, f"copy[{index}]@{match.start()}: forbidden phrase {phrase!r}")
                )
    body = _normalise_text(" ".join(copy_texts))
    for number, disclaimer in enumerate(rules.get("required_disclaimers") or []):
        wanted = _normalise_text(disclaimer)
        if wanted and wanted not in body:
            results.append(("content_brand_rules", False, f"required disclaimer #{number + 1} missing from the copy"))
    if ai_generated:
        disclosure = _normalise_text(rules.get("ai_disclosure"))
        if not disclosure:
            results.append(("content_brand_rules", False, "the brand kit has no ai_disclosure text"))
        elif disclosure not in body:
            results.append(("content_brand_rules", False, "generated copy without the kit's AI disclosure"))
    return results or [("content_brand_rules", True, "the brand kit governs this piece")]


def refusals(results) -> list[str]:
    return [message for _code, passed, message in results if not passed]


# --- rendering the reader-visible text of each artefact ---------------------------------------


def fill(text: str, values: dict, rules: dict) -> str:
    """Resolve every placeholder the way a render would, for text-level checks: variables from
    `values`, the kit's disclaimers, and a neutral token for layout-only namespaces."""

    def substitute(match: re.Match) -> str:
        head, _, rest = match.group(1).partition(".")
        if head == "var":
            return str(values[rest])
        if match.group(1) == "kit.disclaimers":
            return "\n".join(rules["required_disclaimers"])
        if match.group(1) == "kit.ai_disclosure":
            return str(rules.get("ai_disclosure") or "")
        return "x"

    return PLACEHOLDER_RE.sub(substitute, text)


def values_for(source: dict, variant: dict | None = None) -> dict:
    values = {variable["key"]: variable.get("default") for variable in source["variables"]}
    values.update((variant or {}).get("vars") or {})
    return values


def template_texts(source: dict, values: dict, rules: dict) -> list[str]:
    texts = [fill(text, values, rules) for block in source["blocks"] for text in block_strings(block)]
    if source["renderer"] == "xlsx":
        sidecar = _yaml(REPO / "templates" / source["id"] / source["layout"])
        for sheet in sidecar["sheets"]:
            texts.append(sheet["name"])
            texts += [fill(cell["text"], values, rules) for cell in sheet["cells"] if "text" in cell]
    return texts


def book_texts(source: dict, values: dict, rules: dict) -> list[str]:
    texts = [source["title"], source.get("subtitle") or "", source["author"]]
    for chapter in source["chapters"]:
        texts.append(chapter["title"])
        texts += [fill(text, values, rules) for block in chapter["blocks"] for text in block_strings(block)]
    return texts


def quiz_strings(lesson: dict) -> list[str]:
    strings = []
    for question in (lesson.get("quiz") or {}).get("questions", []):
        strings += [question["prompt"], *question["options"], question["answer"], question["explanation"]]
    return strings


def lessons(source: dict) -> list[dict]:
    return [lesson for module in source["modules"] for lesson in module["lessons"]]


def lesson_texts(source: dict, lesson: dict, values: dict, rules: dict) -> list[str]:
    """One lesson's artefact: its reading, its deck and its quiz -- each must stand alone."""
    texts = [lesson["title"], lesson.get("summary") or ""]
    for key in (lesson["reading"], lesson["slides"]):
        texts.append(fill(course_source_text(source, key), values, rules))
    return texts + quiz_strings(lesson)


def course_texts(source: dict, values: dict, rules: dict) -> list[str]:
    texts = [source["title"]]
    for module in source["modules"]:
        texts += [module["title"], module.get("summary") or ""]
    for lesson in lessons(source):
        texts += lesson_texts(source, lesson, values, rules)
    return texts


# Synthetic F8 launch copy -- what the F8 fake generator is expected to emit for each brief
# (the brief copy source is K2's; see the runbook). Fixed strings, no PII, no URL.
F8_PIECES = {
    "terafin-planner-launch": (
        "Anggaran bulanan tidak harus rumit. Lembar Anggaran Terafina membantu keluarga mencatat "
        "rencana dan realisasi pengeluaran setiap pekan, gratis."
    ),
    "terafin-ebook-launch": (
        "Dasar Keuangan Keluarga: lima bab singkat tentang arus uang, anggaran, dana darurat dan "
        "mengelola utang dengan tenang."
    ),
    "terafin-course-launch": (
        "Kelas Literasi Keuangan 101: belajar membaca arus uang keluarga, menyusun anggaran dan "
        "menilai tawaran keuangan dengan kepala dingin."
    ),
}


def f8_texts(brief_key: str, rules: dict) -> list[str]:
    """A generated piece's copy: the launch text, the kit disclaimers, the KIT's AI disclosure
    and the channel's AI disclosure. The disclaimers are appended here by construction -- the
    copy source is K2's -- so the F8 rows below prove the kit's rules bite on this copy, not that
    a generator places them; K2's L9 proves the latter on the real factory_content piece."""
    return [F8_PIECES[brief_key], *rules["required_disclaimers"], rules["ai_disclosure"], CHANNEL_AI_DISCLOSURE]


def artefacts(rules: dict):
    """(label, texts) for EVERY artefact the line produces: each item x variant, each course
    lesson on its own, and each F8 launch piece."""
    for item in line()["items"]:
        for variant in item["variants"]:
            label = f"{item['kind']}:{item['ref']}:{variant['key']}"
            if item["kind"] == "template":
                source = template(item["ref"])
                yield label, template_texts(source, values_for(source, variant), rules)
            elif item["kind"] == "ebook":
                source = book()
                yield label, book_texts(source, values_for(source, variant), rules)
            elif item["kind"] == "course":
                source = course()
                values = values_for(source, variant)
                for lesson in lessons(source):
                    yield f"{label}:{lesson['id']}", lesson_texts(source, lesson, values, rules)
    for brief in line()["launch"]:
        yield f"f8:{brief['brief_key']}", f8_texts(brief["brief_key"], rules)


def reader_strings() -> list[tuple[str, str]]:
    """(where, text) for every committed string a reader can see, unrendered."""
    found = []
    for template_id in TEMPLATES:
        source = template(template_id)
        found += [(template_id, text) for block in source["blocks"] for text in block_strings(block)]
        found.append((template_id, source["title"]))
        layout = (REPO / "templates" / template_id / source["layout"]).read_text()
        if source["renderer"] == "xlsx":
            for sheet in yaml.safe_load(layout)["sheets"]:
                found.append((template_id, sheet["name"]))
                found += [(template_id, cell["text"]) for cell in sheet["cells"] if "text" in cell]
        else:
            body = re.sub(r"<!--.*?-->", "", layout, flags=re.DOTALL)
            body = re.sub(r"<style>.*?</style>", "", body, flags=re.DOTALL)
            found.append((template_id, re.sub(r"<[^>]+>", " ", body)))
    source = book()
    found += [(EBOOK, text) for text in book_texts(source, values_for(source), {"required_disclaimers": []})]
    found.append((EBOOK, (REPO / "ebooks" / EBOOK / "book.md").read_text()))
    source = course()
    found += [(COURSE, text) for text in course_texts(source, values_for(source), {"required_disclaimers": []})]
    for variable_source in (template(BUDGET_PLANNER), template(CASHFLOW_WORKSHEET), book(), course()):
        for variable in variable_source["variables"]:
            found += [(variable_source["id"], str(value)) for value in variable.get("enum") or []]
            found.append((variable_source["id"], str(variable.get("default"))))
    for item in line()["items"]:
        for variant in item["variants"]:
            found += [(f"line:{item['ref']}", str(value)) for value in (variant.get("vars") or {}).values()]
    found += [(f"f8:{key}", text) for key, text in F8_PIECES.items()]
    return found


def word_re(word: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(part) for part in word.split()) + r"(?!\w)", re.IGNORECASE)


# --- the kit this line renders under ----------------------------------------------------------


def test_the_kit_is_the_draft_placeholder_with_the_education_disclaimer():
    rules = kit()["rules"]
    assert kit()["status"] == "draft"
    assert EDUCATION_DISCLAIMER in rules["required_disclaimers"]
    assert any("kit uji coba" in disclaimer for disclaimer in rules["required_disclaimers"])
    assert rules["forbidden_phrases"][0] == ADVICE_PHRASE, "K2's negative variant injects the kit's FIRST phrase"


# --- the two F3 templates ---------------------------------------------------------------------


@pytest.mark.parametrize("template_id", TEMPLATES)
def test_template_validates_and_every_placeholder_resolves(template_id):
    source = template(template_id)
    validator_for(TEMPLATES_SCHEMA).validate(source)
    layout = (REPO / "templates" / template_id / source["layout"]).read_text()
    assert template_resolve_problems(source, layout) == []


def test_the_templates_are_one_workbook_and_one_pdf():
    assert template(BUDGET_PLANNER)["renderer"] == "xlsx"
    assert template(CASHFLOW_WORKSHEET)["kind"] == "pdf"
    assert template(CASHFLOW_WORKSHEET)["renderer"] == "wkhtml"


@pytest.mark.parametrize("template_id", TEMPLATES)
def test_template_is_approved_for_terafin_only_and_uses_the_kits_fonts(template_id):
    source = template(template_id)
    assert source["brand"]["kits"] == ["terafin"]
    assert source["assets"]["fonts"] == {"heading": kit()["fonts"]["heading"], "body": kit()["fonts"]["body"]}
    assert not source["assets"].get("images"), "no image asset is registered for terafin"


@pytest.mark.parametrize("template_id", TEMPLATES)
def test_template_places_the_kit_disclaimers(template_id):
    blocks = template(template_id)["blocks"]
    assert {"type": "disclaimer", "text": "{{kit.disclaimers}}"} in blocks


def test_the_workbook_sidecar_is_closed_vocabulary_data_and_places_the_disclaimer_on_every_sheet():
    source = template(BUDGET_PLANNER)
    sidecar = _yaml(REPO / "templates" / BUDGET_PLANNER / source["layout"])
    declared = {variable["key"] for variable in source["variables"]}
    disclaimer_index = next(i for i, block in enumerate(source["blocks"]) if block["type"] == "disclaimer")
    assert set(sidecar) == {"sheets"}
    for sheet in sidecar["sheets"]:
        assert set(sheet) <= SHEET_KEYS, sheet
        assert sheet["name"] and sheet["cells"]
        for cell in sheet["cells"]:
            assert set(cell) <= CELL_KEYS, cell
            assert sum(source_key in cell for source_key in CELL_SOURCES) == 1, cell
            if "var" in cell:
                assert cell["var"] in declared, cell
            if "block" in cell:
                assert 0 <= cell["block"] < len(source["blocks"]), cell
            if "formula" in cell:
                assert FORMULA_RE.match(cell["formula"]), cell["formula"]
            if "style" in cell:
                assert cell["style"] in SHEET_STYLES, cell["style"]
        assert any(cell.get("block") == disclaimer_index for cell in sheet["cells"]), sheet["name"]


def test_the_workbook_totals_sum_exactly_the_grid_rows():
    """The totals row is SUM over the table's data rows -- the grid block's rows land one per row
    below its header cell, so an off-by-one would silently drop a budget line."""
    source = template(BUDGET_PLANNER)
    sheet = _yaml(REPO / "templates" / BUDGET_PLANNER / source["layout"])["sheets"][0]
    grid = next(cell for cell in sheet["cells"] if cell.get("style") == "grid")
    table = source["blocks"][grid["block"]]
    header_row = int(re.sub(r"[A-Z]+", "", grid["ref"]))
    first, last = header_row + 1, header_row + len(table["rows"])
    formulas = [cell["formula"] for cell in sheet["cells"] if "formula" in cell]
    assert formulas == [f"SUM(B{first}:B{last})", f"SUM(C{first}:C{last})"]


def test_the_worksheet_layout_carries_no_markup_of_its_own():
    source = template(CASHFLOW_WORKSHEET)
    body = (REPO / "templates" / CASHFLOW_WORKSHEET / source["layout"]).read_text().lower()
    for needle in FORBIDDEN_LAYOUT_MARKUP:
        assert needle not in body


# --- the F4 e-book ----------------------------------------------------------------------------


def test_book_validates_resolves_and_ships_both_formats():
    source = book()
    validator_for(EBOOKS_SCHEMA).validate(source)
    assert book_resolve_problems(source) == []
    assert source["outputs"] == ["pdf", "epub"]
    assert source["renderers"] == {"pdf": "typst", "epub": "pandoc"}
    assert source["brand"]["kits"] == ["terafin"]


def test_book_md_is_the_canonical_derivation():
    assert (REPO / "ebooks" / EBOOK / "book.md").read_text() == render_markdown(book())


def test_book_places_the_disclaimer_in_its_first_and_last_chapter():
    chapters = book()["chapters"]
    for chapter in (chapters[0], chapters[-1]):
        assert {"type": "disclaimer", "text": "{{kit.disclaimers}}"} in chapter["blocks"], chapter["id"]


def test_book_fonts_are_the_kits_and_the_logo_is_the_one_new_placeholder_key():
    source = book()
    assert source["assets"]["fonts"] == {"heading": kit()["fonts"]["heading"], "body": kit()["fonts"]["body"]}
    assert source["assets"]["images"] == {"logo": "asset:image-terafin-book-logo"}
    assert source["cover"]["logo"] == "asset:image-terafin-book-logo"


# --- the F5 course ----------------------------------------------------------------------------


def test_course_validates_and_has_no_unresolvable_problem():
    source = course()
    validator_for(COURSES_SCHEMA).validate(source)
    assert course_problems(source) == []
    assert source["brand"]["kits"] == ["terafin"]
    assert len(source["modules"]) == 3


def test_every_lesson_has_a_quiz_and_every_question_explains_itself():
    for lesson in lessons(course()):
        questions = (lesson.get("quiz") or {}).get("questions") or []
        assert questions, f"{lesson['id']} has no quiz"
        for question in questions:
            assert len(question["explanation"].split()) >= 8, f"{lesson['id']}/{question['id']}"


def test_every_course_reading_and_every_deck_places_the_kit_disclaimers():
    source = course()
    for _lesson_id, reading, slides in sources_of(source):
        for key in (reading, slides):
            assert "{{kit.disclaimers}}" in course_source_text(source, key), key


def test_the_course_names_no_video_or_image_asset():
    source = course()
    assert set(source["assets"]) == {"fonts"}
    assert all("video" not in lesson for lesson in lessons(source))


# --- the line file ----------------------------------------------------------------------------


def test_the_line_resolves_against_the_committed_repo():
    data = line()
    assert line_problems(data) == []
    assert data["id"] == LINE_PATH.stem == "terafin"
    assert data["brand_kit"] == "terafin"


def test_the_line_is_free_planners_plus_a_paid_book_and_course():
    items = {(item["kind"], item["ref"]): item for item in line()["items"]}
    assert set(items) == {
        ("template", BUDGET_PLANNER),
        ("template", CASHFLOW_WORKSHEET),
        ("ebook", EBOOK),
        ("course", COURSE),
    }
    assert items[("template", BUDGET_PLANNER)]["price_idr"] == 0
    assert items[("template", BUDGET_PLANNER)]["digital_kind"] == "planner"
    assert items[("template", CASHFLOW_WORKSHEET)]["price_idr"] == 0
    for key, kind in ((("ebook", EBOOK), "book"), (("course", COURSE), "course")):
        assert items[key]["price_idr"] > 0
        assert items[key]["digital_kind"] == kind
        assert items[key]["publish"] == "digital"
    for key in (("template", BUDGET_PLANNER), ("template", CASHFLOW_WORKSHEET)):
        assert items[key]["publish"] == "factory_digital"


def test_the_line_launches_three_f8_briefs_on_valid_channel_shapes():
    launch = line()["launch"]
    assert len(launch) == 3
    assert {brief["brief_key"] for brief in launch} == set(F8_PIECES)
    for brief in launch:
        assert brief["shape"] in CHANNEL_KINDS[brief["platform"]], brief
        assert brief.get("publish_mode", "creator_handoff") == "creator_handoff", "F10 manual schedule only"


def test_the_line_has_two_variants_of_each_template():
    for item in line()["items"]:
        if item["kind"] == "template":
            assert len({variant["key"] for variant in item["variants"]}) == 2, item["ref"]


# --- every artefact passes brand_rules and carries the no-advice disclaimer -------------------


def test_every_artefact_the_line_produces_passes_brand_rules():
    rules = kit()["rules"]
    produced = list(artefacts(rules))
    # 2 templates x 2 variants + 1 book + 5 course lessons + 3 F8 pieces.
    assert len(produced) == 4 + 1 + len(lessons(course())) + 3
    for label, texts in produced:
        assert refusals(brand_rules(texts, rules)) == [], label
        if label.startswith("f8:"):
            assert refusals(content_brand_rules(texts, rules)) == [], label
        assert EDUCATION_DISCLAIMER.casefold() in _normalise_text(" ".join(texts)), label


def test_an_artefact_without_the_disclaimer_is_refused_by_name():
    rules = kit()["rules"]
    source = template(CASHFLOW_WORKSHEET)
    stripped = copy.deepcopy(source)
    stripped["blocks"] = [block for block in stripped["blocks"] if block["type"] != "disclaimer"]
    messages = refusals(brand_rules(template_texts(stripped, values_for(stripped), rules), rules))
    assert messages == ["required disclaimer #1 missing", "required disclaimer #2 missing"]


# --- the advice refusal (spec B11 local e2e, dokploy pre-proof) -------------------------------


def _inject(text: str) -> str:
    return f"{text} Berikut {ADVICE_PHRASE.title()} pilihan pekan ini."


def _injected_template(template_id: str) -> list[str]:
    rules = kit()["rules"]
    source = copy.deepcopy(template(template_id))
    paragraph = next(block for block in source["blocks"] if block["type"] in ("paragraph", "list"))
    if "text" in paragraph:
        paragraph["text"] = _inject(paragraph["text"])
    else:
        paragraph["items"][0] = _inject(paragraph["items"][0])
    return template_texts(source, values_for(source), rules)


def _injected_book() -> list[str]:
    rules = kit()["rules"]
    source = copy.deepcopy(book())
    paragraph = next(block for block in source["chapters"][1]["blocks"] if block["type"] == "paragraph")
    paragraph["text"] = _inject(paragraph["text"])
    return book_texts(source, values_for(source), rules)


def _injected_course() -> list[str]:
    rules = kit()["rules"]
    source = course()
    lesson = lessons(source)[0]
    texts = lesson_texts(source, lesson, values_for(source), rules)
    texts[2] = _inject(texts[2])  # the lesson's reading, as a copy of the committed source
    return texts


def _injected_f8() -> list[str]:
    rules = kit()["rules"]
    texts = f8_texts("terafin-planner-launch", rules)
    texts[0] = _inject(texts[0])
    return texts


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: _injected_template(BUDGET_PLANNER), id="template-xlsx"),
        pytest.param(lambda: _injected_template(CASHFLOW_WORKSHEET), id="template-pdf"),
        pytest.param(_injected_book, id="ebook"),
        pytest.param(_injected_course, id="course"),
        pytest.param(_injected_f8, id="f8-piece"),
    ],
)
def test_a_copy_containing_rekomendasi_saham_refuses_at_check_time_by_name(build):
    results = brand_rules(build(), kit()["rules"])
    failed = [(code, message) for code, passed, message in results if not passed]
    assert len(failed) == 1, failed
    code, message = failed[0]
    assert code == "brand_rules"
    assert message.endswith(f"forbidden phrase {ADVICE_PHRASE!r}")
    # Named by the kit's rule, never by echoing the copy it found the phrase in.
    assert "pekan ini" not in message
    assert "Rekomendasi Saham" not in message


def test_an_f8_piece_refuses_under_both_brand_checks_by_name():
    """On an F8 piece BOTH the kernel `brand_rules` and factory_content's `content_brand_rules`
    see the phrase, so K2 must expect two named refusals there, not one."""
    rules = kit()["rules"]
    texts = _injected_f8()
    failed = [
        (code, message)
        for code, passed, message in brand_rules(texts, rules) + content_brand_rules(texts, rules)
        if not passed
    ]
    assert [code for code, _message in failed] == ["brand_rules", "content_brand_rules"]
    for _code, message in failed:
        assert message.endswith(f"forbidden phrase {ADVICE_PHRASE!r}")
        assert "pekan ini" not in message


def test_a_generated_f8_piece_without_the_kits_ai_disclosure_refuses():
    rules = kit()["rules"]
    texts = [text for text in f8_texts("terafin-course-launch", rules) if text != rules["ai_disclosure"]]
    assert refusals(content_brand_rules(texts, rules)) == ["generated copy without the kit's AI disclosure"]


def test_the_phrase_match_tolerates_case_and_whitespace_but_not_partial_words():
    rules = kit()["rules"]
    disclaimers = list(rules["required_disclaimers"])
    assert refusals(brand_rules(["REKOMENDASI\n  saham hari ini", *disclaimers], rules))
    assert not refusals(brand_rules(["rekomendasisaham", *disclaimers], rules))


# --- education only: nothing a reader sees is advice-shaped -----------------------------------


def test_the_reader_strings_cover_every_source_and_the_lint_bites():
    """Guards the two lints below against passing vacuously: every source contributes text, and
    the lint's matcher does find a planted word."""
    found = reader_strings()
    sources = {where.split(":")[0] for where, _text in found}
    assert {*TEMPLATES, EBOOK, COURSE, "line", "f8"} <= sources
    assert sum(len(text) for _where, text in found) > 15000
    assert word_re("reksa dana").search("Tentang Reksa\nDana.")
    assert not word_re("emas").search("pemasukan")


def test_no_kit_forbidden_phrase_appears_anywhere_a_reader_can_see():
    rules = kit()["rules"]
    for where, text in reader_strings():
        for phrase in rules["forbidden_phrases"]:
            assert not _phrase_re(phrase).search(text), f"{where}: {phrase!r}"


@pytest.mark.parametrize("word", INSTRUMENT_WORDS + RETURN_WORDS + CALL_WORDS)
def test_no_instrument_name_return_promise_or_buy_sell_call(word):
    pattern = word_re(word)
    for where, text in reader_strings():
        assert not pattern.search(text), f"{where}: {word!r}"


def test_no_url_anywhere_in_the_line_or_its_sources():
    paths = [LINE_PATH]
    for template_id in TEMPLATES:
        paths += sorted((REPO / "templates" / template_id).iterdir())
    paths += sorted((REPO / "ebooks" / EBOOK).iterdir())
    paths += sorted((REPO / "courses" / COURSE).iterdir())
    for path in paths:
        assert not URL_RE.search(path.read_text()), path.name


def test_no_synthetic_contact_or_person_data_in_the_sources():
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    phone = re.compile(r"(\+62|\b08)\d{6,}")
    for where, text in reader_strings():
        assert not email.search(text), where
        assert not phone.search(text), where


# --- the example-content header on every file that can carry a comment ------------------------


def _header(path) -> str:
    lines = path.read_text().splitlines()
    return "\n".join(lines[:12])


def test_every_commentable_file_says_example_content():
    paths = [LINE_PATH, REPO / "ebooks" / EBOOK / "ebook.yaml", REPO / "courses" / COURSE / "course.yaml"]
    for template_id in TEMPLATES:
        directory = REPO / "templates" / template_id
        paths += [directory / "template.yaml", directory / template(template_id)["layout"]]
    for path in paths:
        assert EXAMPLE_MARKER in _header(path), path


# --- the runbook (operational gates + what K2 must run) ---------------------------------------


def test_the_runbook_names_the_operational_gates_and_the_k2_run():
    text = RUNBOOK.read_text()
    for needle in (
        "finance educator",
        "OJK",
        "BI",
        "expert_logins",
        "kit uji coba",
        EDUCATION_DISCLAIMER,
        "bin/product-line-acceptance terafin --db odoo_test_tb_b11",
        "experts: synthetic-overlay",
        "buyer@example.test",
    ):
        # Markdown wraps long lines, so compare with whitespace normalised.
        assert _normalise_text(needle) in _normalise_text(text), needle


def test_the_runbook_lists_the_placeholder_logo_key_and_every_brief():
    text = RUNBOOK.read_text()
    # The logo key has no committed bytes and no licence evidence yet: an operational gate, and
    # K2 must register a synthetic asset for it in the test DB.
    assert book()["cover"]["logo"] in text
    for brief in line()["launch"]:
        assert brief["brief_key"] in text, brief["brief_key"]


def test_the_runbook_requires_the_advice_refusal_for_each_kind():
    text = RUNBOOK.read_text()
    assert f"forbidden phrase '{ADVICE_PHRASE}'" in text
    # F8: both brand checks refuse, and generated copy needs the kit's own AI disclosure.
    assert "content_brand_rules" in text
    assert "rules.ai_disclosure" in text
    for kind in ("a template", "the e-book", "the course", "F8 launch piece"):
        assert kind in text, kind
