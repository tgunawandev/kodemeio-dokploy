"""The Terakidz product line B1 (Teracorp Track B, slice TB1; spec section 4 "B1").

TB1 adds three printables (F3 `template.v1`), one starter guide (F4 `ebook.v1`) and the line
file `product_lines/terakidz.yaml` that sells or gives them away together with the existing
learning pack and course. The shared contract suites pin only the sources that shipped with
them (`SHIPPED` in test_contracts_templates / test_contracts_ebooks, which this slice must not
edit), and test_contracts_product_lines checks the line's schema and resolution. This module
holds the TB1 content to the same bar and adds what only a *product line* needs:

1. **Contract** -- each new source validates, every placeholder resolves, the book's `book.md`
   is the canonical derivation, and every declared image role is placed (the consumers'
   importers refuse an unplaced one).
2. **Licence** -- no new asset key: every `asset:` ref is one an already-committed Terakidz
   source uses, so the F3/F4/F5 rollout runbooks' licence evidence covers it.
3. **Voice** -- the kit's forbidden phrases never appear (matched the way the Odoo
   `brand_rules` check matches them: case-insensitive, on word boundaries), none of the medical
   claim vocabulary the kit's `dont` rules exclude appears, no PII pattern appears, and every
   source places the kit's disclaimers. Every file says `EXAMPLE CONTENT` (TB-D9).
4. **Line shape** -- the free/paid split, the two monthly variants with distinct inputs, the
   publish path per kind, and five launch briefs whose shape the channel accepts.
"""

from __future__ import annotations

import re

import pytest
import yaml
from contracts_lib import CONTRACTS, validator_for
from test_contracts_ebooks import EBOOKS_SCHEMA, render_markdown
from test_contracts_ebooks import resolve_problems as book_resolve_problems
from test_contracts_templates import FORBIDDEN_LAYOUT_MARKUP, TEMPLATES_SCHEMA, block_strings
from test_contracts_templates import resolve_problems as template_resolve_problems

REPO = CONTRACTS.parent
KIT = yaml.safe_load((REPO / "brands" / "terakidz.yaml").read_text())
LINE_PATH = REPO / "product_lines" / "terakidz.yaml"

NEW_TEMPLATES = ("terakidz-routine-chart", "terakidz-emotion-cards", "terakidz-monthly-kit")
NEW_BOOKS = ("terakidz-panduan-aac-rumah",)
# The Terakidz sources that shipped before TB1. Their asset keys are the only ones the F3/F4/F5
# rollout runbooks register with licence evidence, so they bound what TB1 may reference.
PRIOR_SOURCES = (
    REPO / "templates" / "terakidz-learning-pack" / "template.yaml",
    REPO / "ebooks" / "terakidz-example" / "ebook.yaml",
    REPO / "courses" / "terakidz-komunikasi-dasar" / "course.yaml",
)

FREE = {
    ("template", "terakidz-learning-pack"),
    ("template", "terakidz-routine-chart"),
    ("template", "terakidz-emotion-cards"),
    ("ebook", "terakidz-panduan-aac-rumah"),
}
PAID = {("template", "terakidz-monthly-kit"), ("course", "terakidz-komunikasi-dasar")}
# kind -> the only publish path the line may use for it (spec 3.2 / 3.4): templates through R6,
# e-books through the K2 adapter (`factory_ebook_digital`) and courses through the F5 publisher.
PUBLISH_BY_KIND = {"template": "factory_digital", "ebook": "digital", "course": "digital"}
DIGITAL_KIND_BY_KIND = {"template": "template_pack", "ebook": "book", "course": "course"}

# A PINNED COPY of the piece kinds each channel accepts, from kodemeio-odoo
# `src/private/factory/factory_content/data/content_channel_rules.yaml` (`kinds`). The dokploy
# schema does not relate platform to shape (K1 hand-off); a launch brief the channel refuses
# would only fail inside the harness, so the line is held to it here.
CHANNEL_KINDS = {
    "tiktok": {"caption", "script", "carousel"},
    "youtube": {"caption", "script"},
    "instagram": {"caption", "script", "carousel"},
    "facebook": {"caption", "script", "carousel"},
}

# The kit's `dont` rules forbid medical claims, promised outcomes and development timelines.
# The kit's `forbidden_phrases` catch the sharpest forms; this list is TB1's own stricter guard
# over its example copy (the kit's own disclaimer is never scanned: sources place it by token).
MEDICAL_CLAIM_WORDS = (
    "sembuh",
    "menyembuhkan",
    "penyembuhan",
    "obat",
    "diagnosa",
    "mendiagnosis",
    "dijamin",
    "menjamin",
    "pasti bisa",
    "terbukti",
    "normal kembali",
)

# The same patterns as the Odoo kernel's `pii` / variable checks (factory_base factory_check.py).
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![0-9+])(\+62|0)8[0-9]{7,12}(?![0-9])")
NIK_RE = re.compile(r"(?<![0-9])[0-9]{16}(?![0-9])")
URL_RE = re.compile(r"(://|(?<![\w.])[Ww]{3}\.)")


def phrase_re(phrase: str) -> re.Pattern:
    """The Odoo `brand_rules` matcher: case-insensitive, whole words, any whitespace between."""
    words = [re.escape(word) for word in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def template_dir(template_id: str):
    return REPO / "templates" / template_id


def template(template_id: str) -> dict:
    return yaml.safe_load((template_dir(template_id) / "template.yaml").read_text())


def layout(template_id: str) -> str:
    return (template_dir(template_id) / template(template_id)["layout"]).read_text()


def book(book_id: str) -> dict:
    return yaml.safe_load((REPO / "ebooks" / book_id / "ebook.yaml").read_text())


def line() -> dict:
    return yaml.safe_load(LINE_PATH.read_text())


def template_texts(payload: dict, layout_text: str) -> list[str]:
    texts = [payload["title"]]
    for block in payload["blocks"]:
        texts += block_strings(block)
    return [*texts, layout_text]


def book_texts(payload: dict, markdown: str) -> list[str]:
    texts = [payload["title"], payload.get("subtitle") or "", payload["author"]]
    for chapter in payload["chapters"]:
        texts.append(chapter["title"])
        for block in chapter["blocks"]:
            texts += block_strings(block)
    return [*texts, markdown]


def all_sources() -> list[tuple[str, list[str], str]]:
    """(label, reader texts, raw file text) for every TB1 source file."""
    found = []
    for template_id in NEW_TEMPLATES:
        raw = (template_dir(template_id) / "template.yaml").read_text()
        found.append((template_id, template_texts(template(template_id), layout(template_id)), raw))
    for book_id in NEW_BOOKS:
        raw = (REPO / "ebooks" / book_id / "ebook.yaml").read_text()
        markdown = (REPO / "ebooks" / book_id / "book.md").read_text()
        found.append((book_id, book_texts(book(book_id), markdown), raw))
    return found


def asset_refs(payload: dict) -> set[str]:
    refs = set()
    for bucket in payload.get("assets", {}).values():
        refs |= set(bucket.values())
    return refs


def voice_problems(texts: list[str]) -> list[str]:
    problems = []
    for phrase in KIT["rules"]["forbidden_phrases"]:
        if any(phrase_re(phrase).search(text) for text in texts):
            problems.append(f"kit forbidden phrase {phrase!r}")
    for word in MEDICAL_CLAIM_WORDS:
        if any(phrase_re(word).search(text) for text in texts):
            problems.append(f"medical-claim word {word!r}")
    return problems


_SOURCES = all_sources() if all((template_dir(t) / "template.yaml").is_file() for t in NEW_TEMPLATES) else []
_SOURCE_IDS = [*NEW_TEMPLATES, *NEW_BOOKS]


# --- 1. contract ---------------------------------------------------------------


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_new_template_validates(template_id):
    validator_for(TEMPLATES_SCHEMA).validate(template(template_id))


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_new_template_placeholders_all_resolve(template_id):
    assert template_resolve_problems(template(template_id), layout(template_id)) == []


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_new_template_id_is_its_directory_and_a_first_version(template_id):
    payload = template(template_id)
    assert payload["id"] == template_id
    assert payload["version"] == 1
    assert payload["kind"] == "pdf" and payload["renderer"] == "wkhtml" and payload["outputs"] == ["pdf"]


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_every_declared_image_role_is_placed(template_id):
    # factory_template refuses "assets.images.<role> is declared and no block places it".
    payload = template(template_id)
    placed = {block["asset"] for block in payload["blocks"] if block["type"] == "image"}
    assert set((payload["assets"].get("images") or {}).values()) <= placed


@pytest.mark.parametrize("template_id", NEW_TEMPLATES)
def test_the_layout_is_a_skeleton_that_places_the_blocks_once(template_id):
    text = layout(template_id)
    assert text.count("{{blocks}}") == 1
    lowered = text.lower()
    assert not [marker for marker in FORBIDDEN_LAYOUT_MARKUP if marker in lowered]
    for needle in ("<img", "src=", "href=", "@import", "url("):
        assert needle not in lowered, f"{template_id} layout carries {needle!r}"


@pytest.mark.parametrize("book_id", NEW_BOOKS)
def test_new_book_validates_and_resolves(book_id):
    payload = book(book_id)
    validator_for(EBOOKS_SCHEMA).validate(payload)
    assert payload["id"] == book_id and payload["version"] == 1
    assert book_resolve_problems(payload) == []


@pytest.mark.parametrize("book_id", NEW_BOOKS)
def test_new_book_md_is_the_canonical_rendering(book_id):
    assert (REPO / "ebooks" / book_id / "book.md").read_text() == render_markdown(book(book_id))


@pytest.mark.parametrize("book_id", NEW_BOOKS)
def test_new_book_ships_both_formats(book_id):
    payload = book(book_id)
    assert set(payload["outputs"]) == {"pdf", "epub"}
    assert payload["renderers"] == {"pdf": "typst", "epub": "pandoc"}


@pytest.mark.parametrize("book_id", NEW_BOOKS)
def test_every_declared_book_image_role_is_placed(book_id):
    # factory_ebook refuses "assets.images.<role> is declared and nothing places it".
    payload = book(book_id)
    placed = {payload["cover"]["logo"]}
    placed |= {block["asset"] for ch in payload["chapters"] for block in ch["blocks"] if block["type"] == "image"}
    assert set((payload["assets"].get("images") or {}).values()) <= placed


# --- 2. licence ----------------------------------------------------------------


def _prior_asset_refs() -> set[str]:
    return set().union(*(asset_refs(yaml.safe_load(path.read_text())) for path in PRIOR_SOURCES))


def test_the_prior_sources_bound_a_non_empty_asset_set():
    assert {"asset:font-nunito", "asset:image-terakidz-book-logo"} <= _prior_asset_refs()


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_no_new_asset_key(source_id):
    payload = template(source_id) if source_id in NEW_TEMPLATES else book(source_id)
    new = asset_refs(payload) - _prior_asset_refs()
    assert not new, f"{source_id} introduces unregistered asset(s) {sorted(new)} (register with licence evidence first)"
    assert payload["assets"]["fonts"] == KIT["fonts"], "the kit's own font refs"


# --- 3. voice --------------------------------------------------------------------


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_source_is_terakidz_only(source_id):
    payload = template(source_id) if source_id in NEW_TEMPLATES else book(source_id)
    assert payload["brand"]["kits"] == ["terakidz"]


@pytest.mark.parametrize("index", range(len(_SOURCE_IDS)), ids=_SOURCE_IDS)
def test_header_marks_example_content(index):
    label, _, raw = _SOURCES[index]
    assert "EXAMPLE CONTENT" in raw.split("\n\n", 1)[0], f"{label} header"


@pytest.mark.parametrize("index", range(len(_SOURCE_IDS)), ids=_SOURCE_IDS)
def test_copy_honours_the_kit_voice(index):
    label, texts, _ = _SOURCES[index]
    assert voice_problems(texts) == [], label


@pytest.mark.parametrize("index", range(len(_SOURCE_IDS)), ids=_SOURCE_IDS)
def test_copy_carries_no_pii_pattern_and_no_url(index):
    label, texts, raw = _SOURCES[index]
    for text in [*texts, raw]:
        for kind, pattern in (("email", EMAIL_RE), ("phone", PHONE_RE), ("nik", NIK_RE), ("url", URL_RE)):
            assert not pattern.search(text), f"{label}: {kind}"


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_source_places_the_kit_disclaimers(source_id):
    if source_id in NEW_TEMPLATES:
        blocks = template(source_id)["blocks"]
    else:
        blocks = [block for chapter in book(source_id)["chapters"] for block in chapter["blocks"]]
    disclaimers = [block for block in blocks if block["type"] == "disclaimer"]
    assert disclaimers and all(block["text"] == "{{kit.disclaimers}}" for block in disclaimers)


def test_the_voice_scanner_is_not_vacuous():
    # L3's dokploy half: a copy with the kit's first forbidden phrase injected is refused, as is
    # a medical claim the kit's phrases do not list.
    payload = template(NEW_TEMPLATES[0])
    forbidden = KIT["rules"]["forbidden_phrases"][0]
    payload["blocks"][1]["text"] += " " + forbidden.upper()
    assert f"kit forbidden phrase {forbidden!r}" in voice_problems(template_texts(payload, ""))
    assert voice_problems(["Anak Anda pasti   bisa bicara."]) == ["medical-claim word 'pasti bisa'"]
    assert voice_problems(["Terapis anak Anda tahu langkah berikutnya."]) == []


# --- 4. the line -------------------------------------------------------------------


def test_line_header_names_example_content_and_the_free_delivery_decision():
    header = LINE_PATH.read_text().split("\n\n", 1)[0]
    assert "EXAMPLE CONTENT" in header
    assert "TB-D8" in header


def test_line_is_the_terakidz_line():
    payload = line()
    assert payload["id"] == "terakidz" and payload["brand_kit"] == "terakidz"
    assert all("kit" not in item for item in payload["items"])


def test_free_and_paid_items_are_exactly_the_b1_set():
    items = {(item["kind"], item["ref"]): item for item in line()["items"]}
    assert set(items) == FREE | PAID
    assert {key for key, item in items.items() if item["price_idr"] == 0} == FREE
    assert all(items[key]["price_idr"] > 0 for key in PAID)


def test_every_item_takes_its_kinds_publish_path():
    # A free item is still published: TB-D8 (a) sells it at zero through the same path, and
    # (b) serves the same released artefact as a public download -- either way it is released.
    for item in line()["items"]:
        assert item["publish"] == PUBLISH_BY_KIND[item["kind"]], item["ref"]
        assert item["digital_kind"] == DIGITAL_KIND_BY_KIND[item["kind"]], item["ref"]


def _effective_vars(item: dict, variant: dict) -> dict:
    kind_dir, filename = {"template": ("templates", "template.yaml"), "ebook": ("ebooks", "ebook.yaml")}.get(
        item["kind"], ("courses", "course.yaml")
    )
    source = yaml.safe_load((REPO / kind_dir / item["ref"] / filename).read_text())
    values = {spec["key"]: spec.get("default") for spec in source.get("variables") or []}
    values.update(variant.get("vars") or {})
    return values


def test_the_monthly_kit_ships_two_distinct_month_editions():
    spec = next(
        variable for variable in template("terakidz-monthly-kit")["variables"] if variable["key"] == "edition_month"
    )
    assert spec["type"] == "enum" and spec["required"] is True and len(spec["enum"]) >= 2
    item = next(item for item in line()["items"] if item["ref"] == "terakidz-monthly-kit")
    assert len(item["variants"]) == 2
    months = [variant["vars"]["edition_month"] for variant in item["variants"]]
    assert len(set(months)) == 2 and set(months) <= set(spec["enum"])


def test_every_variant_of_an_item_is_a_distinct_render_input():
    # Two variants with the same effective variables would render the same `input_sha256`.
    for item in line()["items"]:
        seen = [tuple(sorted(_effective_vars(item, variant).items())) for variant in item["variants"]]
        assert len(seen) == len(set(seen)), item["ref"]


def test_launch_is_five_manual_briefs_in_carousel_and_caption():
    launch = line()["launch"]
    assert len(launch) == 5
    assert {entry["shape"] for entry in launch} == {"carousel", "caption"}
    assert all(entry["publish_mode"] == "creator_handoff" for entry in launch), "F10 manual schedule"


def test_every_launch_shape_is_one_its_channel_accepts():
    for entry in line()["launch"]:
        assert entry["shape"] in CHANNEL_KINDS[entry["platform"]], entry["brief_key"]


def test_every_new_source_is_on_the_line():
    refs = {item["ref"] for item in line()["items"]}
    assert set(NEW_TEMPLATES) | set(NEW_BOOKS) <= refs
