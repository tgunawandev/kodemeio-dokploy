"""Pytest config for deploys/ tests.

Adds the deploys/ directory to sys.path so tests can `import generate`, and
the deploys/tests/ directory itself so tests can `import contracts_lib`.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DEPLOYS_DIR = TESTS_DIR.parent
for directory in (DEPLOYS_DIR, TESTS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
