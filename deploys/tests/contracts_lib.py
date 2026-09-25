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


def _walk_schema(
    node: object,
    reg: Registry,
    path: str,
    paths: set[str],
    required_paths: set[str],
    seen_refs: frozenset[str],
) -> None:
    """Depth-first walk of a schema, recording every property path and every
    path that is required by its immediate parent.

    Descends through `properties`, `items` (dict or list form), and
    `allOf`/`anyOf`/`oneOf` members (at the same path, since each member
    constrains the same instance location), resolving `$ref` via `reg` and
    guarding against reference cycles with `seen_refs`.
    """
    if not isinstance(node, dict):
        return

    ref = node.get("$ref")
    if ref is not None:
        if ref in seen_refs:
            return
        seen_refs = seen_refs | {ref}
        _walk_schema(reg.contents(ref), reg, path, paths, required_paths, seen_refs)

    required_here = set(node.get("required", []))
    for key, subschema in node.get("properties", {}).items():
        child_path = f"{path}.{key}" if path else key
        paths.add(child_path)
        if key in required_here:
            required_paths.add(child_path)
        _walk_schema(subschema, reg, child_path, paths, required_paths, seen_refs)

    items = node.get("items")
    if isinstance(items, dict):
        _walk_schema(items, reg, f"{path}[]", paths, required_paths, seen_refs)
    elif isinstance(items, list):
        for index, item_schema in enumerate(items):
            _walk_schema(item_schema, reg, f"{path}[{index}]", paths, required_paths, seen_refs)

    for keyword in ("allOf", "anyOf", "oneOf"):
        for member in node.get(keyword) or []:
            _walk_schema(member, reg, path, paths, required_paths, seen_refs)


def schema_property_paths(schema: dict, reg: Registry) -> tuple[set[str], set[str]]:
    """Every property path in `schema` (e.g. `payload.lines[].quantity`),
    and the subset of those paths required by their immediate parent,
    resolving `$ref` via `reg` and descending through `properties`, `items`,
    and `allOf`/`anyOf`/`oneOf`.

    Returns `(paths, required_paths)`.
    """
    paths: set[str] = set()
    required_paths: set[str] = set()
    _walk_schema(schema, reg, "", paths, required_paths, frozenset())
    return paths, required_paths


def schema_property_names(schema: dict, reg: Registry) -> set[str]:
    """Every property NAME appearing anywhere in `schema` (paths flattened to
    their final segment), resolving `$ref` via `reg`. Used for denylist-style
    checks (e.g. PII property names) that don't care about nesting depth.
    """
    paths, _ = schema_property_paths(schema, reg)
    return {path.rsplit(".", 1)[-1] for path in paths}
