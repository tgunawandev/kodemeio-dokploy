"""Placeholder brand kits (Track B, slice K1; spec section 3.1).

Terakona, Terafina and Terakode have no approved brand yet, so their kits are `status: draft`
PLACEHOLDERS. The guard lives in DATA, never in the kernel: every draft kit must carry a required
disclaimer containing `kit uji coba` ("trial kit"), and `factory.check.brand_rules` already refuses
any artefact that omits a required disclaimer. So everything rendered under a placeholder kit
visibly says it is a trial brand, and an accidental release can never pass for real brand copy.

Checked for EVERY committed kit (`brands/*.yaml`), so a kit added later meets the same rules:

* a draft kit is visibly a placeholder (its header says so and it carries the `PLACEHOLDER`
  marker), has no expert reviewers, and carries a `kit uji coba` required disclaimer;
* an `active` kit never carries the `PLACEHOLDER` marker;
* every kit validates, names its fonts as declared assets, never forbids its own mandatory copy
  and meets WCAG AA on its colour pairs;
* the Terafina kit carries the "no advice" law as data (forbidden phrases plus the education
  disclaimer).

Kept in its own module so it never touches the contract test modules other slices edit.
"""

from __future__ import annotations

import re

import pytest
import yaml
from contracts_lib import CONTRACTS, validator_for

BRANDS = CONTRACTS.parent / "brands"
BRAND_KIT_SCHEMA = CONTRACTS / "brands" / "brand_kit.v1.schema.json"

MARKER = "PLACEHOLDER"
TRIAL = "kit uji coba"
TERAFIN_EDUCATION_DISCLAIMER = "Konten edukasi, bukan nasihat keuangan atau ajakan berinvestasi."
# The plan's minimum set (K1). The kit may forbid more; it may never forbid fewer.
TERAFIN_NO_ADVICE = (
    "rekomendasi saham",
    "pasti untung",
    "sinyal trading",
    "beli sekarang",
    "dijamin untung",
    "bebas risiko",
)
# The two kits this slice adds. They are held to the strict header form (first line).
K1_KITS = ("terafin", "terakod")

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _all_kit_names() -> list[str]:
    return sorted(path.stem for path in BRANDS.glob("*.yaml"))


def _source(name: str) -> str:
    return (BRANDS / f"{name}.yaml").read_text()


def _kit(name: str) -> dict:
    return yaml.safe_load(_source(name))


def _header(name: str) -> list[str]:
    """The leading comment block: every line before the first YAML key."""
    lines = []
    for line in _source(name).splitlines():
        if line.startswith("#"):
            lines.append(line)
        elif line.strip():
            break
    return lines


def _kits_with_status(status: str) -> list[str]:
    return [name for name in _all_kit_names() if _kit(name)["status"] == status]


ALL_KITS = _all_kit_names()
DRAFT_KITS = _kits_with_status("draft")
ACTIVE_KITS = _kits_with_status("active")


# --- the population itself --------------------------------------------------


def test_the_placeholder_kits_exist_and_are_drafts():
    for name in ("terakona", *K1_KITS):
        assert name in DRAFT_KITS, f"brands/{name}.yaml must be a status: draft placeholder kit"


def test_terakidz_stays_the_one_active_kit():
    assert ACTIVE_KITS == ["terakidz"]


@pytest.mark.parametrize("name", ALL_KITS)
def test_every_kit_validates(name):
    validator_for(BRAND_KIT_SCHEMA).validate(_kit(name))


@pytest.mark.parametrize("name", ALL_KITS)
def test_kit_code_is_its_file_stem(name):
    # Product lines and templates name a kit by its code; the file must be findable by it.
    assert _kit(name)["code"] == name


# --- draft kits: visibly placeholders ------------------------------------------


@pytest.mark.parametrize("name", DRAFT_KITS)
def test_draft_kit_header_says_placeholder(name):
    header = _header(name)
    assert header, f"brands/{name}.yaml has no header comment block"
    assert any("placeholder" in line.lower() for line in header), f"brands/{name}.yaml header never says placeholder"
    assert MARKER in _source(name), f"brands/{name}.yaml carries no {MARKER} marker"


@pytest.mark.parametrize("name", K1_KITS)
def test_new_placeholder_kits_start_with_the_marker(name):
    assert _source(name).splitlines()[0].startswith(f"# {MARKER}"), f"brands/{name}.yaml line 1"


@pytest.mark.parametrize("name", DRAFT_KITS)
def test_draft_kit_carries_a_trial_kit_disclaimer(name):
    disclaimers = _kit(name)["rules"]["required_disclaimers"]
    assert any(TRIAL in disclaimer.lower() for disclaimer in disclaimers), (
        f"draft kit {name} has no required disclaimer containing {TRIAL!r}: "
        "an artefact rendered under it could pass for real brand content"
    )


@pytest.mark.parametrize("name", DRAFT_KITS)
def test_draft_kit_names_no_expert_reviewers(name):
    # Naming real experts is an operational gate; a placeholder kit never has any.
    assert _kit(name)["reviewers"]["expert_logins"] == []


@pytest.mark.parametrize("name", ACTIVE_KITS)
def test_active_kit_carries_no_placeholder_marker(name):
    assert MARKER not in _source(name), f"active kit {name} still carries {MARKER}"


@pytest.mark.parametrize("name", ACTIVE_KITS)
def test_active_kit_carries_no_trial_kit_disclaimer(name):
    disclaimers = _kit(name)["rules"]["required_disclaimers"]
    assert not any(TRIAL in disclaimer.lower() for disclaimer in disclaimers)


# --- every kit: the rules the renderer and brand_rules check rely on ---------


@pytest.mark.parametrize("name", ALL_KITS)
def test_mandatory_copy_never_contains_the_kits_own_forbidden_phrases(name):
    # brand_rules matches forbidden phrases case-insensitively; the kit's own disclaimers and
    # AI disclosure are inserted into every artefact, so they must never trip it.
    kit = _kit(name)
    forbidden = [phrase.lower() for phrase in kit["rules"]["forbidden_phrases"]]
    mandatory = [*kit["rules"]["required_disclaimers"], kit["rules"]["ai_disclosure"]]
    for text in mandatory:
        hits = [phrase for phrase in forbidden if phrase in text.lower()]
        assert not hits, f"{name} mandatory copy contains forbidden phrase(s) {hits}: {text!r}"


@pytest.mark.parametrize("name", ALL_KITS)
def test_every_font_is_a_declared_asset(name):
    kit = _kit(name)
    for role, ref in kit["fonts"].items():
        assert ref in kit["assets"], f"{name}.fonts.{role}={ref} not declared in assets"


@pytest.mark.parametrize("name", K1_KITS)
def test_new_kits_reuse_existing_font_asset_keys_only(name):
    # No new font asset without licence evidence: reuse a key another kit already registers.
    others = {ref for other in ALL_KITS if other not in K1_KITS for ref in _kit(other)["fonts"].values()}
    for ref in _kit(name)["fonts"].values():
        assert ref in others, f"{name} introduces font asset {ref} with no licence evidence on file"


@pytest.mark.parametrize("name", K1_KITS)
def test_new_kits_have_a_distinct_primary_colour(name):
    primaries = {other: _kit(other)["colors"]["primary"].lower() for other in ALL_KITS}
    mine = primaries.pop(name)
    assert mine not in primaries.values(), f"{name} reuses another kit's primary colour"


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    r, g, b = (_linear(int(value[i : i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.mark.parametrize("name", K1_KITS)
def test_new_kit_colour_pairs_meet_wcag_aa(name):
    colors = _kit(name)["colors"]
    pairs = [(key, f"{key}_foreground") for key in colors if f"{key}_foreground" in colors]
    pairs.append(("background", "foreground"))
    assert len(pairs) >= 5
    for base, fg in pairs:
        assert HEX_RE.match(colors[base]) and HEX_RE.match(colors[fg]), f"{name} {base}/{fg} is not hex"
        ratio = _contrast(colors[base], colors[fg])
        assert ratio >= 4.5, f"{name} {base}/{fg} contrast {ratio:.2f} < 4.5"


# --- Terafina: the "no advice" law as data --------------------------------------


def test_terafin_forbids_the_advice_phrases():
    forbidden = {phrase.lower() for phrase in _kit("terafin")["rules"]["forbidden_phrases"]}
    missing = [phrase for phrase in TERAFIN_NO_ADVICE if phrase not in forbidden]
    assert not missing, f"terafin kit does not forbid {missing}"


def test_terafin_requires_the_education_disclaimer():
    disclaimers = _kit("terafin")["rules"]["required_disclaimers"]
    assert TERAFIN_EDUCATION_DISCLAIMER in disclaimers
    assert any(TRIAL in disclaimer.lower() for disclaimer in disclaimers)


def test_terafin_does_not_forbid_the_bare_word_dijamin():
    # Honest finance education says "tidak dijamin" ("not guaranteed"); a bare-word match would
    # refuse exactly the risk warning the line exists to teach. The claim shape is forbidden.
    forbidden = {phrase.lower() for phrase in _kit("terafin")["rules"]["forbidden_phrases"]}
    assert "dijamin" not in forbidden
    assert "dijamin untung" in forbidden


@pytest.mark.parametrize("name", ["terafin", "terakod"])
def test_placeholder_contact_is_synthetic(name):
    # No real number: the all-zero subscriber part is the house placeholder form.
    assert re.fullmatch(r"\+628120+", _kit(name)["contact"]["whatsapp"])
