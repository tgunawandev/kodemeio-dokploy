"""Tests for the factory_job work_order.v1 widening and brand_kit.v1 (Teracorp
factory commons, task 1). Kept in its own module so it never touches the
governance/events test modules other agents are concurrently editing.
"""

from __future__ import annotations

import re

import jsonschema
import pytest
import yaml
from contracts_lib import CONTRACTS, load, registry, schema_property_names, validator_for

BRANDS = CONTRACTS.parent / "brands"
WORK_ORDER_EXAMPLES = CONTRACTS / "examples" / "work_orders"
WORK_ORDER_SCHEMA = CONTRACTS / "work_orders" / "work_order.v1.schema.json"
BRAND_KIT_SCHEMA = CONTRACTS / "brands" / "brand_kit.v1.schema.json"

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Same denylist shape as test_contracts_governance.py's PII_NAMES/PII_PREFIXES,
# duplicated locally (not imported) so this module never depends on a file
# another agent is concurrently editing.
_PII_NAMES = {"email", "phone", "name", "full_name", "address", "dob", "birth_date", "nik", "ktp"}
_PII_PREFIXES = ("child_",)


def _pii_hits(names):
    return {n for n in names if n in _PII_NAMES or n.startswith(_PII_PREFIXES)}


def _kit(name: str) -> dict:
    return yaml.safe_load((BRANDS / f"{name}.yaml").read_text())


def _color_pairs(colors: dict) -> list[tuple[str, str]]:
    """(base_key, foreground_key) pairs present in a kit's colors mapping."""
    pairs = []
    for key in colors:
        if key.endswith("_foreground"):
            continue
        fg_key = f"{key}_foreground"
        if fg_key in colors:
            pairs.append((key, fg_key))
    return pairs


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = _linear(r), _linear(g), _linear(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# --- work_order.v1: factory_job widening ------------------------------------


def test_order_kind_still_validates_without_any_new_optional_field():
    # Locks in "additive only": the pre-existing `order` shape, untouched.
    instance = {
        "work_order_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "brand": "terakidz",
        "kind": "order",
        "correlation_id": "wo-order-0001",
        "state": "executing",
        "refs": {"odoo_sale_order": "odoo:sale.order/7"},
    }
    validator_for(WORK_ORDER_SCHEMA).validate(instance)


def test_factory_job_valid_example_validates():
    validator_for(WORK_ORDER_SCHEMA).validate(load(WORK_ORDER_EXAMPLES / "work_order.v1.valid.json"))


def test_factory_job_valid_example_uses_the_new_optional_fields():
    instance = load(WORK_ORDER_EXAMPLES / "work_order.v1.valid.json")
    assert instance["kind"] == "factory_job"
    assert instance["factory"] == "website"
    assert instance["gate"] in {"auto", "expert", "founder"}
    assert instance["refs"]["odoo_factory_work_order"].startswith("odoo:factory.work.order/")
    assert instance["refs"]["odoo_landing_page_version"].startswith("odoo:landing.page.version/")


def test_factory_job_invalid_kind_example_fails_on_the_kind_enum():
    instance = load(WORK_ORDER_EXAMPLES / "factory_job.invalid-kind.json")
    errors = list(validator_for(WORK_ORDER_SCHEMA).iter_errors(instance))
    assert errors, "factory_job.invalid-kind.json unexpectedly validated"
    matching = [
        e for e in errors if e.validator == "enum" and tuple(e.absolute_path) == ("kind",) and "'factory'" in e.message
    ]
    assert matching, [e.message for e in errors]


@pytest.mark.parametrize(
    "bad_ref",
    [
        {"odoo_factory_work_order": "factory.work.order/101"},  # missing odoo: prefix
        {"odoo_landing_page_version": "odoo:landing.page.version/abc"},  # non-numeric id
    ],
)
def test_new_refs_reject_malformed_values(bad_ref):
    instance = {
        "work_order_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "brand": "terakidz",
        "kind": "factory_job",
        "correlation_id": "wo-factory-0001",
        "state": "executing",
        "refs": bad_ref,
    }
    with pytest.raises(jsonschema.ValidationError):
        validator_for(WORK_ORDER_SCHEMA).validate(instance)


@pytest.mark.parametrize("schema_path", [WORK_ORDER_SCHEMA, BRAND_KIT_SCHEMA], ids=lambda p: p.name)
def test_no_pii_property_names(schema_path):
    names = schema_property_names(load(schema_path), registry())
    assert not _pii_hits(names), _pii_hits(names)


# --- brand_kit.v1 + the terakidz/terakon kits -------------------------------


@pytest.mark.parametrize("name", ["terakidz", "terakon"])
def test_brand_kit_validates(name):
    validator_for(BRAND_KIT_SCHEMA).validate(_kit(name))


def test_terakidz_is_active_and_terakon_is_draft():
    assert _kit("terakidz")["status"] == "active"
    assert _kit("terakon")["status"] == "draft"


def test_terakidz_has_no_expert_reviewers_yet_founder_fills():
    assert _kit("terakidz")["reviewers"]["expert_logins"] == []


def test_terakidz_forbidden_phrases_and_required_disclaimer():
    kit = _kit("terakidz")
    for phrase in ("menyembuhkan autisme", "terapi", "diagnosis"):
        assert phrase in kit["rules"]["forbidden_phrases"]
    assert (
        "Terakidz adalah layanan edukasi, bukan layanan diagnosis atau terapi." in kit["rules"]["required_disclaimers"]
    )


@pytest.mark.parametrize("name", ["terakidz", "terakon"])
def test_every_font_reference_is_a_declared_asset(name):
    kit = _kit(name)
    assets = set(kit["assets"])
    for font_key, asset_ref in kit["fonts"].items():
        assert asset_ref in assets, f"{name}.fonts.{font_key}={asset_ref} not declared in assets"


def test_terakidz_and_terakon_differ_in_primary_color_and_heading_font():
    terakidz, terakon = _kit("terakidz"), _kit("terakon")
    assert terakidz["colors"]["primary"] != terakon["colors"]["primary"]
    assert terakidz["fonts"]["heading"] != terakon["fonts"]["heading"]


_CONTRAST_CASES = [
    (kit_name, base, fg) for kit_name in ("terakidz", "terakon") for base, fg in _color_pairs(_kit(kit_name)["colors"])
]


@pytest.mark.parametrize(
    "kit_name,base,fg",
    _CONTRAST_CASES,
    ids=[f"{k}:{b}/{f}" for k, b, f in _CONTRAST_CASES],
)
def test_color_pair_meets_wcag_aa_contrast(kit_name, base, fg):
    colors = _kit(kit_name)["colors"]
    base_value, fg_value = colors[base], colors[fg]
    if not (HEX_RE.match(base_value) and HEX_RE.match(fg_value)):
        pytest.skip(f"{kit_name}.{base}/{fg} is not a hex pair (oklch or rem) — contrast is only checked for hex")
    ratio = _contrast_ratio(base_value, fg_value)
    assert ratio >= 4.5, f"{kit_name} {base}/{fg} contrast {ratio:.2f} < 4.5"
