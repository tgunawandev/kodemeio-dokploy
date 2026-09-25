"""Shared loaders for contracts/ schema tests."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def iter_schemas() -> list[Path]:
    return sorted(CONTRACTS.rglob("*.schema.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def registry() -> Registry:
    resources = []
    for path in iter_schemas():
        schema = load(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def validator_for(schema_path: Path) -> Draft202012Validator:
    return Draft202012Validator(load(schema_path), registry=registry())
