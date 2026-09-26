"""FRIDAY's repos must be a subset of the DSH broker's example repo allowlist.

``contracts/agents/friday.yaml``'s ``repos:`` says which repos FRIDAY may open
draft PRs against. The DSH PR broker (kodemeio-dsh ``broker/server.py``) is
FRIDAY's only write path to GitHub, and it refuses any repo not named in
``DSH_BROKER_REPOS``.

What this checks is kodemeio-dsh's committed ``.env.example`` (never a real
``.env``), NOT the ``DSH_BROKER_REPOS`` actually deployed on the broker host:
the example is the reviewed source of truth operators copy from, so drift
between it and friday.yaml is caught at review time. The live value can
still differ and must be set from the example at deploy time.

kodemeio-dsh is a sibling checkout (``<workspace>/kodemeio-dsh/``, i.e. this
repo's parent directory), the same layout and the same
``CI_GATES_REQUIRED_SIBLINGS`` rule as ``test_ci_gates.py``:

    - sibling present -> assert the subset relation.
    - sibling absent, and not (CI=true and ``kodemeio-dsh`` listed in
      ``CI_GATES_REQUIRED_SIBLINGS``) -> skip.
    - sibling absent, CI=true, and listed as required -> FAIL: the CI job
      said it checked the sibling out, so its absence is a checkout defect.

kodemeio-dokploy's ``validate.yml`` checks kodemeio-dsh out next to this repo
and sets ``CI_GATES_REQUIRED_SIBLINGS: kodemeio-dsh``, so in CI this runs.
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
REQUIRED_IN_CI = {name.strip() for name in os.environ.get("CI_GATES_REQUIRED_SIBLINGS", "").split(",") if name.strip()}
SIBLING = "kodemeio-dsh"


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


def missing_sibling_verdict(repo_name: str, *, ci: bool, required: set[str]) -> str:
    """ "fail" or "skip" for a sibling that is not checked out: "fail" only
    when CI=true AND ``repo_name`` is in ``required``
    (CI_GATES_REQUIRED_SIBLINGS), otherwise "skip". Same rule as
    ``test_ci_gates.missing_sibling_verdict``."""
    if ci and repo_name in required:
        return "fail"
    return "skip"


def test_friday_repos_are_on_the_broker_allowlist():
    if not DSH_ENV_EXAMPLE.is_file():
        message = f"kodemeio-dsh/.env.example not found at {DSH_ENV_EXAMPLE}"
        if missing_sibling_verdict(SIBLING, ci=CI, required=REQUIRED_IN_CI) == "fail":
            pytest.fail(f"{message} -- listed in CI_GATES_REQUIRED_SIBLINGS: checkout defect, not nothing-to-check")
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


def test_missing_sibling_verdict_skips_unless_ci_and_required():
    assert missing_sibling_verdict("kodemeio-dsh", ci=False, required=set()) == "skip"
    assert missing_sibling_verdict("kodemeio-dsh", ci=True, required=set()) == "skip"
    assert missing_sibling_verdict("kodemeio-dsh", ci=False, required={"kodemeio-dsh"}) == "skip"
    assert missing_sibling_verdict("kodemeio-dsh", ci=True, required={"kodemeio-odoo"}) == "skip"


def test_missing_sibling_verdict_fails_when_ci_and_required():
    assert missing_sibling_verdict("kodemeio-dsh", ci=True, required={"kodemeio-dsh"}) == "fail"
