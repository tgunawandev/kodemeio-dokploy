"""course.v1 and the shipped example course (course factory, F5/CRS1 task 1). Kept in
its own module so it never touches the governance/events/factory/template/ebook test modules
other agents are concurrently editing.

Two layers are checked, and the second one is the point:

1. **Schema** -- `course.v1.schema.json` against the shipped
   `courses/<id>/{course.yaml,sources}` files, the examples under `contracts/examples/courses/`,
   and one invalid fixture per refusal the factory relies on (a `.py` source, a raw URL, an
   unknown key, a quiz without an explanation, a video reference that is not an asset key).
2. **Semantics the schema cannot express** -- every `{{...}}` placeholder in a source resolves,
   every source KEY a lesson names is declared and exists on disk (and every declared source is
   used), every lesson's quiz answer is verbatim one of its options, and every source stays
   inside the closed Markdown vocabulary the renderer implements. A schema-valid course that
   names `{{var.typo}}`, an undeclared source or an answer no option carries would render a
   broken lesson or ship a quiz that cannot be marked; all of them fail here.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml
from contracts_lib import CONTRACTS, contracts_base_ref, load, pii_hits, registry, schema_property_names, validator_for

COURSES_SCHEMA = CONTRACTS / "courses" / "course.v1.schema.json"
SCHEMA_ID = "https://kodeme.io/contracts/courses/course.v1.schema.json"
EXAMPLES = CONTRACTS / "examples" / "courses"
COURSE_DIR = CONTRACTS.parent / "courses"
BRANDS_DIR = CONTRACTS.parent / "brands"

# The one committed course this contract exists for (spec D6: one example, three modules).
SHIPPED = ("terakidz-komunikasi-dasar",)

# `{{name}}` / `{{name.with.dots}}` -- the whole placeholder vocabulary of course.v1.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")

# The namespaces a course source may address. `theme` never appears INSIDE a source's prose --
# the deck's own styling reads those tokens -- but it is a namespace the renderer supplies.
SOURCE_NAMESPACES = frozenset({"var", "theme", "option", "kit", "course", "lesson"})
COURSE_TOKENS = frozenset({"title", "level", "locale"})
LESSON_TOKENS = frozenset({"title", "id", "module"})
KIT_TOKENS = frozenset({"disclaimers", "ai_disclosure"})
OPTION_NAMES = frozenset({"locale", "palette", "page_size"})
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

# 🔴 The closed Markdown vocabulary (spec D2: a source is Markdown, never a program, and the
# renderer builds every tag itself). A line outside it is REFUSED BY NAME by the renderer -- it
# is never silently dropped -- so the shipped sources are checked against the same rule here.
CODE_FENCE = ("```", "~~~")
TABLE_LINE = re.compile(r"^\s*\|")
HTML_LINE = re.compile(r"^\s*<")
IMAGE_LINE = re.compile(r"^\s*!")
LINK_SYNTAX = re.compile(r"\]\(")
FOREIGN_BULLET = re.compile(r"^\s*[*+] ")
HEADING = re.compile(r"^(#{1,9})\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+\.\s+\S")
BULLET = re.compile(r"^\s*- \S")
QUOTE = re.compile(r"^\s*> \S")
SLIDE_BREAK = re.compile(r"^\s*-{3,}\s*$")
MAX_HEADING_LEVEL = 3


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text())


def course_yaml(course_id: str) -> dict:
    return _yaml(COURSE_DIR / course_id / "course.yaml")


def source_text(course: dict, key: str) -> str:
    return (COURSE_DIR / course["id"] / course["sources"][key]).read_text()


def sources_of(course: dict) -> list[tuple[str, str, str]]:
    """``(lesson id, reading key, slides key)`` for every lesson, in order."""
    return [
        (lesson["id"], lesson["reading"], lesson["slides"])
        for module in course["modules"]
        for lesson in module["lessons"]
    ]


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def errors(payload: dict) -> list:
    return list(validator_for(COURSES_SCHEMA).iter_errors(payload))


def errors_under(payload: dict, prefix: tuple) -> list:
    """Errors at `prefix` or anywhere beneath it."""
    return [error for error in errors(payload) if tuple(error.absolute_path)[: len(prefix)] == prefix]


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def markdown_problems(text: str, *, slides: bool) -> list[str]:
    """Every line of a source the renderer would refuse, by name.

    The renderer's vocabulary is closed: `#`/`##`/`###` headings, `- ` bullets, `1. ` ordered
    items, `> ` quotes, plain paragraphs, and (in a SLIDES source only) `---` between slides.
    Code fences, tables, raw HTML, image syntax, Markdown link syntax and `*`/`+` bullets are
    refused rather than rendered as literal text, so a course can never carry a construct whose
    meaning the reader would have to guess.
    """
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(fence) for fence in CODE_FENCE):
            problems.append(f"line {number}: a code fence is outside the source vocabulary")
        elif TABLE_LINE.match(line):
            problems.append(f"line {number}: a table is outside the source vocabulary")
        elif HTML_LINE.match(line):
            problems.append(f"line {number}: raw HTML is outside the source vocabulary")
        elif IMAGE_LINE.match(stripped):
            problems.append(f"line {number}: image syntax is outside the source vocabulary")
        elif LINK_SYNTAX.search(line):
            problems.append(f"line {number}: link syntax is outside the source vocabulary")
        elif FOREIGN_BULLET.match(line):
            problems.append(f"line {number}: bullets are written with '- ', not '* ' or '+ '")
        elif SLIDE_BREAK.match(line):
            if not slides:
                problems.append(f"line {number}: '---' separates slides and is refused in a reading")
        else:
            heading = HEADING.match(stripped)
            if heading and len(heading.group(1)) > MAX_HEADING_LEVEL:
                problems.append(f"line {number}: a heading deeper than level {MAX_HEADING_LEVEL}")
    return problems


def source_problems(course: dict, key: str, *, slides: bool) -> list[str]:
    """Placeholder + vocabulary problems of one source, all of them named."""
    text = source_text(course, key)
    declared = {variable["key"] for variable in course["variables"]}
    problems = [f"{key}: {problem}" for problem in markdown_problems(text, slides=slides)]
    for name in sorted(placeholders(text)):
        head, _, rest = name.partition(".")
        if head == "var" and rest not in declared:
            problems.append(f"{key}: {{{{{name}}}}} is not a declared variable")
        elif head == "course" and rest not in COURSE_TOKENS:
            problems.append(f"{key}: {{{{{name}}}}} is not a course token")
        elif head == "lesson" and rest not in LESSON_TOKENS:
            problems.append(f"{key}: {{{{{name}}}}} is not a lesson token")
        elif head == "kit" and rest not in KIT_TOKENS:
            problems.append(f"{key}: {{{{{name}}}}} is not kit content a course may place")
        elif head == "option" and rest not in OPTION_NAMES:
            problems.append(f"{key}: {{{{{name}}}}} is not a render option")
        elif head == "theme" and rest not in THEME_TOKENS:
            problems.append(f"{key}: {{{{{name}}}}} is not a brand-kit theme token")
        elif head not in SOURCE_NAMESPACES:
            problems.append(f"{key}: {{{{{name}}}}} has no namespace the renderer supplies")
    return problems


def quiz_problems(course: dict) -> list[str]:
    """Every quiz whose answer is not one of its own options, or whose options are degenerate.

    The consumer refuses these BY NAME at materialise time (`factory.course`'s quiz check); the
    schema can express the shape but not option membership, which is exactly why this exists.
    """
    problems = []
    for module in course["modules"]:
        for lesson in module["lessons"]:
            quiz = lesson.get("quiz")
            if not quiz:
                continue
            if not 0 <= quiz["pass_score"] <= 100:
                problems.append(f"{lesson['id']}: pass_score is outside 0-100")
            seen = set()
            for question in quiz["questions"]:
                if question["id"] in seen:
                    problems.append(f"{lesson['id']}/{question['id']}: question id is used twice")
                seen.add(question["id"])
                if question["answer"] not in question["options"]:
                    problems.append(f"{lesson['id']}/{question['id']}: the answer is not one of the options")
                if len(set(question["options"])) != len(question["options"]):
                    problems.append(f"{lesson['id']}/{question['id']}: an option is listed twice")
                if not question["explanation"].strip():
                    problems.append(f"{lesson['id']}/{question['id']}: the explanation is empty")
    return problems


def course_problems(course: dict) -> list[str]:
    """Every problem in the shipped course that the schema cannot see."""
    problems = []
    declared = set(course["sources"])
    used = set()
    for lesson_id, reading, slides in sources_of(course):
        for role, key in (("reading", reading), ("slides", slides)):
            used.add(key)
            if key not in declared:
                problems.append(f"{lesson_id}: {role} names the undeclared source '{key}'")
                continue
            if not (COURSE_DIR / course["id"] / course["sources"][key]).exists():
                problems.append(f"{lesson_id}: {role} names '{key}', which is not beside course.yaml")
                continue
            problems += source_problems(course, key, slides=(role == "slides"))
    for key in sorted(declared - used):
        problems.append(f"source {key} is declared and never used by a lesson")
    # A declared variable nobody places is a variant axis that does nothing -- and it would make
    # "two locales, two documents" silently untrue (found by this check on the first run of the
    # locale variant test, 2026-09-27).
    used_variables = set()
    for key in sorted(used & declared):
        text = source_text(course, key)
        for name in placeholders(text):
            head, _sep, rest = name.partition(".")
            if head == "var":
                used_variables.add(rest)
    declared_variables = {variable["key"] for variable in course["variables"]}
    for key in sorted(declared_variables - used_variables):
        problems.append(f"variable {key} is declared and no source places it")
    for module in course["modules"]:
        for lesson in module["lessons"]:
            video = lesson.get("video")
            if video and video["asset"] not in set((course["assets"].get("footage") or {}).values()):
                problems.append(f"{lesson['id']}: video {video['asset']} is not declared in assets.footage")
    return problems + quiz_problems(course)


# --- the shipped course ------------------------------------------------------


@pytest.mark.parametrize("course_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_course_validates(course_id):
    validator_for(COURSES_SCHEMA).validate(course_yaml(course_id))


@pytest.mark.parametrize("course_id", SHIPPED, ids=list(SHIPPED))
def test_shipped_course_has_no_unresolvable_problem(course_id):
    assert course_problems(course_yaml(course_id)) == []


@pytest.mark.parametrize("course_id", SHIPPED, ids=list(SHIPPED))
def test_the_shipped_course_is_three_modules(course_id):
    # Spec D6: one example course, three modules -- enough to prove the outline, the quiz and
    # the per-lesson artefacts without becoming a curriculum.
    course = course_yaml(course_id)
    assert len(course["modules"]) == 3
    assert sum(len(module["lessons"]) for module in course["modules"]) >= 3


@pytest.mark.parametrize("course_id", SHIPPED, ids=list(SHIPPED))
def test_every_lesson_carries_the_kits_disclaimers(course_id):
    """The kit's required disclaimer is placed by the course itself (`{{kit.disclaimers}}` in the
    lesson's slides): the kernel's `brand_rules` check then finds the words in the rendered deck,
    and a course that forgot them is refused rather than released."""
    course = course_yaml(course_id)
    for _lesson_id, _reading, slides in sources_of(course):
        assert "{{kit.disclaimers}}" in source_text(course, slides)


def test_the_example_matches_the_shipped_yaml():
    # The example is derived from the shipped file; this pins them together so a change to one
    # without the other fails here rather than at a consumer.
    assert example("course.v1.valid.json") == course_yaml("terakidz-komunikasi-dasar")


def test_the_example_validates():
    validator_for(COURSES_SCHEMA).validate(example("course.v1.valid.json"))


def test_every_asset_reference_is_an_asset_key():
    for course_id in SHIPPED:
        course = course_yaml(course_id)
        buckets = course["assets"]
        refs = list(buckets["fonts"].values())
        refs += list((buckets.get("images") or {}).values())
        refs += list((buckets.get("footage") or {}).values())
        refs += [
            lesson["video"]["asset"]
            for module in course["modules"]
            for lesson in module["lessons"]
            if lesson.get("video")
        ]
        assert refs
        for ref in refs:
            assert re.fullmatch(r"asset:[a-z0-9-]+", ref), f"{course_id}: {ref}"
        assert "https://" not in (COURSE_DIR / course_id / "course.yaml").read_text()


def test_supported_kits_are_committed_kits():
    for course_id in SHIPPED:
        assert set(course_yaml(course_id)["brand"]["kits"]) <= {path.stem for path in BRANDS_DIR.glob("*.yaml")}


def test_the_quiz_shape_holds_in_the_shipped_course():
    for course_id in SHIPPED:
        course = course_yaml(course_id)
        quizzes = [lesson for module in course["modules"] for lesson in module["lessons"] if lesson.get("quiz")]
        assert quizzes, "the shipped course carries at least one quiz"
        assert quiz_problems(course) == []


# --- refusals ----------------------------------------------------------------


def test_a_source_is_markdown_never_a_program():
    # 🔴 Spec D2's F5 half: the `sources` values are pinned to `.md`, so a `.py` (or `.html`)
    # source is refused by the contract before any consumer reads the file.
    payload = example("course.v1.invalid-python-source.json")
    assert errors_under(payload, ("sources",)), "a .py source validated"
    schema = load(COURSES_SCHEMA)
    pattern = schema["properties"]["sources"]["additionalProperties"]["pattern"]
    assert ".py" not in pattern and "\\.md$" in pattern
    for course_id in SHIPPED:
        for name in course_yaml(course_id)["sources"].values():
            assert name.endswith(".md"), name


def test_an_undeclared_source_key_fails_the_semantic_check():
    payload = example("course.v1.invalid-undeclared-source-key.json")
    # Schema-valid on purpose: only the cross-reference check can catch it, which is why it exists.
    validator_for(COURSES_SCHEMA).validate(payload)
    problems = course_problems(payload)
    assert any("m9-missing" in problem for problem in problems), problems


def test_a_quiz_answer_that_is_not_an_option_fails_the_semantic_check():
    payload = example("course.v1.invalid-quiz-answer-not-an-option.json")
    validator_for(COURSES_SCHEMA).validate(payload)
    problems = quiz_problems(payload)
    assert any("not one of the options" in problem for problem in problems), problems


def test_a_quiz_without_an_explanation_is_refused_by_the_schema():
    assert errors_under(example("course.v1.invalid-quiz-without-explanation.json"), ("modules",)), (
        "a quiz question without an explanation validated"
    )


def test_raw_url_fixture_fails_on_the_font_bucket():
    payload = example("course.v1.invalid-raw-url.json")
    assert errors_under(payload, ("assets",)), "a raw URL was accepted as a font reference"
    payload["assets"]["fonts"]["body"] = "asset:font-nunito"
    assert errors(payload) == []


def test_a_video_reference_that_is_not_an_asset_key_is_refused():
    payload = example("course.v1.invalid-video-not-an-asset.json")
    assert errors_under(payload, ("modules",)), "an https video URL validated"


def test_an_unknown_top_level_key_fails():
    assert errors_under(example("course.v1.invalid-unknown-key.json"), ()), "an unknown top-level key validated"


def test_a_course_with_no_modules_is_refused():
    assert errors_under(example("course.v1.invalid-empty-modules.json"), ("modules",)), "an empty course validated"


def test_the_markdown_vocabulary_is_closed():
    # The renderer refuses a construct it cannot render, by name; this pins the rule the shipped
    # sources satisfy and the shape a future source must stay inside.
    slides = "# A heading\n\n---\n\n- a bullet\n\n1. an ordered item\n\n> a quote\n\nA paragraph.\n"
    assert markdown_problems(slides, slides=True) == []
    for text, reason in (
        ("```\ncode\n```\n", "code fence"),
        ("| a | b |\n", "table"),
        ("<b>bold</b>\n", "raw HTML"),
        ("![alt](asset:image-x)\n", "image syntax"),
        ("see [the guide](https://example.test)\n", "link syntax"),
        ("* bullet\n", "bullets"),
        ("#### deep\n", "deeper than level 3"),
    ):
        found = markdown_problems(text, slides=True)
        assert found and any(reason.split()[0] in problem for problem in found), (text, found)
    assert markdown_problems("a\n\n---\n\nb\n", slides=False), "'---' is refused in a reading"


def test_no_pii_property_names_in_the_course_schema():
    names = schema_property_names(load(COURSES_SCHEMA), registry())
    assert not pii_hits(names), pii_hits(names)


def test_the_schema_is_registered_under_its_contracts_path():
    assert load(COURSES_SCHEMA)["$id"] == SCHEMA_ID


def test_schema_is_backward_compatible_with_base():
    base = contracts_base_ref()
    if base is None:
        pytest.skip("not a git checkout; nothing to compare against")
    rel = COURSES_SCHEMA.relative_to(CONTRACTS.parent).as_posix()
    shown = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=CONTRACTS.parent, capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"new schema since {base}, nothing to compare")
    assert json.loads(shown.stdout) == load(COURSES_SCHEMA)
