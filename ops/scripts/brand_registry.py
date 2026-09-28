#!/usr/bin/env python3
"""The brand registry is data: a brand code is valid iff ``brands/<code>.yaml`` exists.

Offline validators import this instead of pinning a closed brand list, so adding a brand is a
data change (one committed kit file), never a code or schema edit.
"""

from __future__ import annotations

import re
from pathlib import Path

BRANDS_DIR = Path(__file__).resolve().parents[2] / "brands"
BRAND_CODE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


def known_brands(brands_dir: Path | None = None) -> frozenset[str]:
    """Codes of the committed brand kits (``<code>.yaml`` stems that are valid brand slugs)."""
    directory = BRANDS_DIR if brands_dir is None else brands_dir
    if not directory.is_dir():
        return frozenset()
    return frozenset(path.stem for path in directory.glob("*.yaml") if BRAND_CODE.fullmatch(path.stem))
