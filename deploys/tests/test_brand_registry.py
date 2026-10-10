"""The brand registry is DATA: a brand code is valid iff `brands/<code>.yaml` exists.

Contracts pin only the brand slug shape (never a closed enum), and this module cross-checks
every committed reference against the registry, so adding a brand is one kit file and no schema
or code edit (plan 2026-09-28, section 6 H1). Kept in its own module so it never touches the
contract test modules other slices edit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from contracts_lib import CONTRACTS, load, validator_for

REPO = CONTRACTS.parent
BRANDS_DIR = REPO / "brands"
SLUG = "^[a-z][a-z0-9-]{1,31}$"
RESERVED_PROFILE_BRANDS = {"internal"}  # an agent that serves no brand

BRAND_FIELDS = [
    (CONTRACTS / "work_orders" / "work_order.v1.schema.json", ("properties", "brand")),
    (CONTRACTS / "brands" / "brand_kit.v1.schema.json", ("properties", "brand")),
    (CONTRACTS / "agents" / "profile.v1.schema.json", ("properties", "brand")),
    (REPO / "ops" / "contracts" / "hook_library.v1.schema.json", ("$defs", "brand")),
    (REPO / "ops" / "contracts" / "hook_performance_window.v1.schema.json", ("properties", "brand")),
]


def registry() -> set[str]:
    return {path.stem for path in BRANDS_DIR.glob("*.yaml")}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_registry_holds_the_current_brands():
    assert {"terakidz", "terakona", "terafin", "terakod"} <= registry()


@pytest.mark.parametrize("path,where", BRAND_FIELDS, ids=lambda v: v.name if isinstance(v, Path) else "/".join(v))
def test_brand_is_a_slug_never_a_closed_enum(path, where):
    node = load(path)
    for key in where:
        node = node[key]
    assert "enum" not in node and "const" not in node, f"{path.name}: brand must not be a closed list"
    assert node["pattern"] == SLUG


@pytest.mark.parametrize("path", sorted(BRANDS_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_kit_names_a_registered_brand(path):
    kit = _yaml(path)
    assert kit["code"] == path.stem
    assert kit["brand"] in registry(), f"{path.name}: brand {kit['brand']!r} has no brands/<code>.yaml"


@pytest.mark.parametrize("path", sorted((CONTRACTS / "agents").glob("*.yaml")), ids=lambda p: p.name)
def test_every_agent_profile_names_a_registered_brand(path):
    profile = _yaml(path)
    if "brand" in profile:
        assert profile["brand"] in registry() | RESERVED_PROFILE_BRANDS


def test_work_order_examples_name_registered_brands():
    valid = json.loads((CONTRACTS / "examples" / "work_orders" / "work_order.v1.valid.json").read_text())
    assert valid["brand"] in registry()


def test_the_schema_admits_a_new_brand_slug_and_the_registry_decides():
    schema = CONTRACTS / "work_orders" / "work_order.v1.schema.json"
    example = json.loads((CONTRACTS / "examples" / "work_orders" / "work_order.v1.valid.json").read_text())
    assert not list(validator_for(schema).iter_errors({**example, "brand": "newbrand"}))
    assert "newbrand" not in registry()
    assert list(validator_for(schema).iter_errors({**example, "brand": "New Brand"}))
