"""Shared loaders for contracts/ schema tests."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"

# The canonical PII property-name denylist, shared by every schema/property-name
# test across modules (events, factory contracts, ...) so it is declared once.
PII_NAMES = {"email", "phone", "name", "full_name", "address", "dob", "birth_date", "nik", "ktp"}
# Any property about a child (child_name, child_age, ...) is `child` class data,
# which classification.yaml never allows in events.
PII_PREFIXES = ("child_",)


def pii_hits(names: Iterable[str]) -> set[str]:
    return {n for n in names if n in PII_NAMES or n.startswith(PII_PREFIXES)}


def contracts_base_ref(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    env: Mapping[str, str] = os.environ,
) -> str | None:
    """The git ref the schema backward-compatibility check compares against.

    Comparing against ``HEAD`` is vacuous in CI, where the checked-out tree IS
    ``HEAD``. In order: ``CONTRACTS_BASE_REF`` when set and non-empty; else
    ``git merge-base HEAD origin/main`` unless that is ``HEAD`` itself (a push
    to main); else ``HEAD~1``.

    Returns ``None`` when git or the repository is unavailable locally (the
    caller skips). In CI (``CI=true``) that raises instead: a compat check that
    silently skips there is the failure mode this helper exists to prevent.
    """
    explicit = env.get("CONTRACTS_BASE_REF", "").strip()
    if explicit:
        return explicit

    def git(*args: str) -> str | None:
        try:
            done = run(["git", *args], cwd=CONTRACTS.parent, capture_output=True, text=True)
        except OSError:
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    head = git("rev-parse", "HEAD")
    if head is None:
        if env.get("CI", "").lower() == "true":
            raise RuntimeError("git (or the repository history) is unavailable in CI; compat check cannot run")
        return None
    merge_base = git("merge-base", "HEAD", "origin/main")
    if merge_base and merge_base != head:
        return merge_base
    return "HEAD~1"


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
