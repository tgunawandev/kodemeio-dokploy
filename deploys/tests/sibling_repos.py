"""Resolve legacy local archives while preserving explicit CI sibling checkouts."""

import os
from pathlib import Path


def sibling_repo(workspace_root: Path, name: str) -> Path:
    sibling = workspace_root / name
    if os.environ.get("CI", "").strip().lower() == "true":
        return sibling
    archive = workspace_root.parent / "kodemeio-archived" / name
    if name in {"kodemeio-dsh", "kodemeio-llmlite"} and not sibling.exists() and archive.is_dir():
        return archive
    return sibling
