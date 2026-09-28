"""Founder rule 2026-09-28: the program name never appears in this repo's paths or tracked text.

Brand names (config/data/fixtures) are fine; the umbrella program word is not. The pattern is
built from pieces at runtime so this guard never matches itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PATTERN = "".join(("tera", "[ _.-]?", "corp"))
TERM = re.compile(PATTERN, re.I)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs a git checkout"
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def test_no_program_name_in_tracked_paths():
    files = _git("ls-files").stdout.splitlines()
    assert files
    assert not [f for f in files if TERM.search(f)]


def test_no_program_name_in_tracked_text():
    result = _git("grep", "-iIl", "-E", PATTERN)
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout == "", result.stdout
