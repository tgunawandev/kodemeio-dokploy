"""FRIDAY's repos must be a subset of the DSH broker's repo allowlist.

``contracts/agents/friday.yaml``'s ``repos:`` says which repos FRIDAY may open
draft PRs against. The DSH PR broker (kodemeio-dsh ``broker/server.py``) is
FRIDAY's only write path to GitHub, and it refuses any repo not named in
``DSH_BROKER_REPOS``. A repo on FRIDAY's list but not on the broker's would
fail at task-creation time, in production, after an issue was approved. This
test catches that drift when the change is made.

The broker allowlist is read from kodemeio-dsh's committed ``.env.example``
(never a real ``.env``). kodemeio-dsh is a sibling checkout
(``kodemeio-workspace/kodemeio-dsh/``), the same layout ``test_ci_gates.py``
relies on:

    - sibling present -> assert the subset relation.
    - sibling absent, CI unset -> skip (a contributor laptop without it).
    - sibling absent, CI=true -> FAIL. A skipped check reads as green in CI,
      and this one must never silently not run there.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
FRIDAY_PROFILE = REPO_ROOT / "contracts" / "agents" / "friday.yaml"
DSH_ENV_EXAMPLE = WORKSPACE_ROOT / "kodemeio-dsh" / ".env.example"

CI = os.environ.get("CI", "").strip().lower() == "true"


def broker_repos(env_example_text: str) -> set[str]:
    """The comma-separated DSH_BROKER_REPOS value from .env.example text."""
    values = [
        line.split("=", 1)[1].strip()
        for line in env_example_text.splitlines()
        if line.strip().startswith("DSH_BROKER_REPOS=")
    ]
    if len(values) != 1:
        raise ValueError(f"expected exactly one DSH_BROKER_REPOS= line, found {len(values)}")
    return {repo.strip() for repo in values[0].split(",") if repo.strip()}


def missing_sibling_verdict(*, ci: bool) -> str:
    """ "fail" under CI=true, else "skip"."""
    return "fail" if ci else "skip"


def test_friday_repos_are_on_the_broker_allowlist():
    if not DSH_ENV_EXAMPLE.is_file():
        message = f"kodemeio-dsh/.env.example not found at {DSH_ENV_EXAMPLE}"
        if missing_sibling_verdict(ci=CI) == "fail":
            pytest.fail(f"{message} -- CI=true, so this check must run, not skip")
        pytest.skip(message)

    friday_repos = set(yaml.safe_load(FRIDAY_PROFILE.read_text())["repos"])
    allowed = broker_repos(DSH_ENV_EXAMPLE.read_text())
    missing = sorted(friday_repos - allowed)
    assert not missing, f"friday.yaml repos not in DSH_BROKER_REPOS: {missing}"


def test_broker_repos_parses_the_single_assignment():
    text = "# comment\nGITHUB_OWNER=x\nDSH_BROKER_REPOS=kodemeio-a, kodemeio-b,,kodemeio-c\nGITHUB_TOKEN=\n"
    assert broker_repos(text) == {"kodemeio-a", "kodemeio-b", "kodemeio-c"}


@pytest.mark.parametrize("text", ["GITHUB_OWNER=x\n", "DSH_BROKER_REPOS=a\nDSH_BROKER_REPOS=b\n"])
def test_broker_repos_rejects_missing_or_duplicate_line(text):
    with pytest.raises(ValueError):
        broker_repos(text)


def test_missing_sibling_verdict_fails_only_in_ci():
    assert missing_sibling_verdict(ci=True) == "fail"
    assert missing_sibling_verdict(ci=False) == "skip"
