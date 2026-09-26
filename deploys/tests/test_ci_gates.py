"""Static checker for Teracorp SW1 / FRIDAY's CI gates (plan task 5).

FRIDAY opens draft PRs against sibling repos in this workspace
(``contracts/agents/friday.yaml``'s ``repos:``; ``kodemeio-llmlite`` and
``kodemeio-hatchet`` joined that list and the broker allowlist by ruling R3,
and ``GATE_WORKFLOW`` below covers the repos whose PR gate this plan task
added or edited). Before FRIDAY exists, "a PR gate runs" is just a
workflow file; this module is the thing that actually reads each sibling's
PR-triggered workflow(s) and asserts four rules mechanically, the same way
the plan's Global Constraints describe a real gate:

    1. triggers on ``pull_request``
    2. declares ``permissions`` (job- or top-level) with ``contents: read``
    3. no gating job carries ``continue-on-error: true``
    4. every job's ``runs-on`` is a GitHub-hosted label, not self-hosted

Sibling repos are plain directories next to this one
(``kodemeio-workspace/<name>/``), each its own git checkout -- there is no
multi-repo checkout mechanism today. So for each expected sibling:

    - present locally (regardless of CI) -> parse its workflows and assert
      the four rules against every one that triggers on ``pull_request``.
    - absent locally, and *not* named in ``CI_GATES_REQUIRED_SIBLINGS``
      -> skip. This is the common case: kodemeio-dokploy's own CI checks out
      only itself, so on a real CI run every sibling is "absent" today, and
      that must never be silently read as "all gates pass" -- it reads as
      "not checked, tell me why".
    - absent locally, CI=true, AND named in ``CI_GATES_REQUIRED_SIBLINGS``
      -> fail. This is for a future CI job that deliberately checks out
      siblings alongside this repo; if it says a sibling should be there and
      it isn't, that is a checkout defect, not "nothing to check".

``CI_GATES_REQUIRED_SIBLINGS`` is unset by default (nothing here checks out
siblings yet), so today this always skips a missing sibling even under
CI=true -- which is honest, not a loophole: nothing currently claims those
repos are checked out, so nothing here should pretend otherwise.

The rule-checking function (``gate_violations``) is also proven directly
against a fixture of kodemeio-react's PR workflow file BEFORE this same plan
task edited it (``fixtures/ci_gates/react-pull-request.original.yml``,
byte-identical to the last commit that shipped `continue-on-error: true` on
its `quality` job) -- so "the checker actually rejects the bad shape" does
not depend on any sibling being checked out at all.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
FIXTURES = TESTS_DIR / "fixtures" / "ci_gates"

CI = os.environ.get("CI", "").strip().lower() == "true"
REQUIRED_IN_CI = {name.strip() for name in os.environ.get("CI_GATES_REQUIRED_SIBLINGS", "").split(",") if name.strip()}

# The one PR-gate workflow this plan task added or edited per sibling repo.
# Deliberately scoped to exactly these files, not "every workflow that
# happens to trigger on pull_request in that repo": kodemeio-react alone
# carries two more (app-kit-scaffold.yml, terakidz-deploy.yml) that are
# pre-existing, out of this task's scope, and nobody's asked this checker to
# audit. "The repos FRIDAY will open PRs against" (task-5-brief.md) names A
# gate per repo -- this mapping is it. Not read from
# contracts/agents/friday.yaml's `repos:` list either (kodemeio-dokploy and
# kodemeio-hatchet are on it but their gates are not this task's), and
# "keep it simple" argues against wiring a second contract's schema into a
# test that has nothing to do with dev tasks or agent profiles.
GATE_WORKFLOW = {
    "kodemeio-odoo": "pr-gate.yml",
    "kodemeio-next": "ci.yml",
    "kodemeio-react": "pull-request.yml",
    "kodemeio-llmlite": "ci.yml",
}
SIBLING_REPOS = tuple(GATE_WORKFLOW)

GITHUB_HOSTED_RUNS_ON = re.compile(r"^(ubuntu|windows|macos)-(latest|\d{2}(?:\.\d{2})?)$")


def _triggers_on_pull_request(on_value: object) -> bool:
    """`on:` normalizes to a bare string, a list of event names, or a dict
    keyed by event name. PyYAML's YAML-1.1 resolver also reads an unquoted
    `on:` key itself as the boolean `True`, not the string "on" -- every
    real workflow in this workspace hits that, so callers pass
    `doc.get(True, doc.get("on"))`, not `doc.get("on")` alone."""
    if isinstance(on_value, str):
        return on_value == "pull_request"
    if isinstance(on_value, (list, dict)):
        return "pull_request" in on_value
    return False


def _on_value(doc: dict) -> object:
    return doc.get(True, doc.get("on"))


def _has_contents_read(permissions: object) -> bool:
    return isinstance(permissions, dict) and permissions.get("contents") == "read"


def _is_github_hosted(runs_on: object) -> bool:
    if isinstance(runs_on, str):
        return bool(GITHUB_HOSTED_RUNS_ON.match(runs_on))
    if isinstance(runs_on, list):
        # A GitHub-hosted job names exactly one label; a list is how a
        # self-hosted job spells multiple required labels (e.g.
        # ["self-hosted", "linux"]), which must never match here even if
        # one entry happens to look GitHub-hosted.
        return len(runs_on) == 1 and _is_github_hosted(runs_on[0])
    return False


def gate_violations(doc: dict) -> list[str]:
    """The four rules against one parsed workflow document. Empty means it
    passes; each entry is a human-readable reason it doesn't."""
    violations: list[str] = []

    if not _triggers_on_pull_request(_on_value(doc)):
        violations.append("does not trigger on `pull_request`")

    jobs = doc.get("jobs") or {}
    if not jobs:
        violations.append("defines no `jobs`")

    top_level_ok = _has_contents_read(doc.get("permissions"))

    for job_name, job in jobs.items():
        job = job or {}

        job_permissions = job.get("permissions")
        if job_permissions is not None:
            # Job-level `permissions` REPLACES the top-level block for that
            # job in GitHub Actions -- it does not merge -- so a job that
            # declares its own must repeat `contents: read` itself.
            if not _has_contents_read(job_permissions):
                violations.append(f"job '{job_name}': overrides `permissions` without `contents: read`")
        elif not top_level_ok:
            violations.append(f"job '{job_name}': no `permissions.contents: read` (job- or top-level)")

        if job.get("continue-on-error") is True:
            violations.append(f"job '{job_name}': `continue-on-error: true` on a job that gates the PR")

        runs_on = job.get("runs-on")
        if not _is_github_hosted(runs_on):
            violations.append(f"job '{job_name}': runs-on {runs_on!r} is not a GitHub-hosted label")

    return violations


def pr_triggered_workflows(workflows_dir: Path) -> list[tuple[Path, dict]]:
    """Every *.yml/*.yaml in `workflows_dir` whose `on:` includes
    `pull_request`, parsed, as (path, workflow_dict) pairs."""
    found: list[tuple[Path, dict]] = []
    paths = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    for path in paths:
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and _triggers_on_pull_request(_on_value(doc)):
            found.append((path, doc))
    return found


def _sibling_dir(repo_name: str) -> Path:
    return WORKSPACE_ROOT / repo_name


def _sibling_workflows_dir(repo_name: str) -> Path | None:
    workflows_dir = _sibling_dir(repo_name) / ".github" / "workflows"
    return workflows_dir if workflows_dir.is_dir() else None


def missing_sibling_verdict(repo_name: str, *, ci: bool, required: set[str]) -> str:
    """ "fail" or "skip" -- the verdict for a sibling repo that is not
    checked out locally, given whether CI=true and which repo names
    CI_GATES_REQUIRED_SIBLINGS declares as required. Pulled out as its own
    pure function so the CI=true-and-required branch is provable without
    ever having to make a real sibling checkout disappear."""
    if ci and repo_name in required:
        return "fail"
    return "skip"


# --- live checks against each sibling repo present in this workspace ---


@pytest.mark.parametrize("repo_name", SIBLING_REPOS)
def test_sibling_pr_gate_passes_the_four_rules(repo_name):
    workflows_dir = _sibling_workflows_dir(repo_name)
    if workflows_dir is None:
        if missing_sibling_verdict(repo_name, ci=CI, required=REQUIRED_IN_CI) == "fail":
            pytest.fail(
                f"{repo_name} is listed in CI_GATES_REQUIRED_SIBLINGS but "
                f"{_sibling_dir(repo_name)} does not exist -- checkout defect, not nothing-to-check"
            )
        pytest.skip(f"{repo_name} not checked out locally at {_sibling_dir(repo_name)}")

    gate_name = GATE_WORKFLOW[repo_name]
    by_name = {path.name: doc for path, doc in pr_triggered_workflows(workflows_dir)}
    if gate_name not in by_name:
        pytest.fail(
            f"{repo_name}: expected PR gate '{gate_name}' under {workflows_dir} "
            "is missing, or does not trigger on pull_request"
        )

    violations = gate_violations(by_name[gate_name])
    assert not violations, f"{gate_name}: {violations}"


def test_missing_sibling_verdict_skips_by_default():
    # Today's actual state: nothing sets CI_GATES_REQUIRED_SIBLINGS, so a
    # missing sibling always skips, CI=true or not.
    assert missing_sibling_verdict("kodemeio-react", ci=False, required=set()) == "skip"
    assert missing_sibling_verdict("kodemeio-react", ci=True, required=set()) == "skip"


def test_missing_sibling_verdict_fails_only_when_ci_and_required():
    assert missing_sibling_verdict("kodemeio-react", ci=True, required={"kodemeio-react"}) == "fail"
    # Named as required but CI isn't set: still just a skip (this is what a
    # contributor's laptop looks like even with the env var lying around).
    assert missing_sibling_verdict("kodemeio-react", ci=False, required={"kodemeio-react"}) == "skip"
    # CI=true but a DIFFERENT repo is required: this one is still a plain skip.
    assert missing_sibling_verdict("kodemeio-react", ci=True, required={"kodemeio-odoo"}) == "skip"


# --- fixture proof: the checker rejects the shape it is meant to reject ---


def test_gate_violations_rejects_the_original_react_workflow():
    original = yaml.safe_load((FIXTURES / "react-pull-request.original.yml").read_text())
    pr_docs = [doc for doc in [original] if _triggers_on_pull_request(_on_value(doc))]
    assert pr_docs, "fixture regressed: it must still trigger on pull_request to prove anything"

    violations = gate_violations(original)
    assert any("continue-on-error" in v for v in violations), violations
    assert any("permissions" in v for v in violations), violations


# --- unit tests for the individual rules, independent of any fixture/sibling ---


def test_triggers_on_pull_request_handles_bool_key_string_list_and_dict_forms():
    assert _triggers_on_pull_request("pull_request") is True
    assert _triggers_on_pull_request(["push", "pull_request"]) is True
    assert _triggers_on_pull_request({"pull_request": {"branches": ["main"]}}) is True
    assert _triggers_on_pull_request("push") is False
    assert _triggers_on_pull_request({"push": None}) is False
    assert _triggers_on_pull_request(None) is False


def test_on_value_prefers_the_yaml_bool_key_pyyaml_actually_produces():
    # This is what yaml.safe_load(open(<real workflow>)) hands back for an
    # unquoted `on:` key -- True, not the string "on" -- confirmed against
    # every workflow this task touched.
    assert _on_value({True: "pull_request", "on": "push"}) == "pull_request"
    assert _on_value({"on": "pull_request"}) == "pull_request"


def test_gate_violations_flags_missing_top_level_permissions():
    doc = {
        "on": "pull_request",
        "jobs": {"build": {"runs-on": "ubuntu-latest"}},
    }
    violations = gate_violations(doc)
    assert any("permissions" in v for v in violations)


def test_gate_violations_flags_job_override_without_contents_read():
    doc = {
        "on": "pull_request",
        "permissions": {"contents": "read"},
        "jobs": {"build": {"runs-on": "ubuntu-latest", "permissions": {"packages": "write"}}},
    }
    violations = gate_violations(doc)
    assert any("overrides `permissions`" in v for v in violations)


def test_gate_violations_passes_job_override_that_repeats_contents_read():
    doc = {
        "on": "pull_request",
        "permissions": {"contents": "read"},
        "jobs": {
            "build": {
                "runs-on": "ubuntu-latest",
                "permissions": {"contents": "read", "packages": "write"},
            }
        },
    }
    assert gate_violations(doc) == []


def test_gate_violations_flags_continue_on_error_true():
    doc = {
        "on": "pull_request",
        "permissions": {"contents": "read"},
        "jobs": {"build": {"runs-on": "ubuntu-latest", "continue-on-error": True}},
    }
    violations = gate_violations(doc)
    assert any("continue-on-error" in v for v in violations)


def test_gate_violations_ignores_continue_on_error_false():
    doc = {
        "on": "pull_request",
        "permissions": {"contents": "read"},
        "jobs": {"build": {"runs-on": "ubuntu-latest", "continue-on-error": False}},
    }
    assert gate_violations(doc) == []


def test_gate_violations_flags_self_hosted_runs_on():
    doc = {
        "on": "pull_request",
        "permissions": {"contents": "read"},
        "jobs": {"build": {"runs-on": ["self-hosted", "linux"]}},
    }
    violations = gate_violations(doc)
    assert any("not a GitHub-hosted label" in v for v in violations)


def test_gate_violations_flags_no_trigger():
    doc = {
        "on": "workflow_dispatch",
        "permissions": {"contents": "read"},
        "jobs": {"build": {"runs-on": "ubuntu-latest"}},
    }
    violations = gate_violations(doc)
    assert any("pull_request" in v for v in violations)


@pytest.mark.parametrize(
    "runs_on,expected",
    [
        ("ubuntu-latest", True),
        ("ubuntu-22.04", True),
        ("windows-latest", True),
        ("macos-14", True),
        (["ubuntu-latest"], True),
        (["self-hosted", "linux"], False),
        ("my-custom-runner", False),
        (None, False),
    ],
)
def test_is_github_hosted_table(runs_on, expected):
    assert _is_github_hosted(runs_on) is expected


def test_pr_triggered_workflows_filters_by_trigger(tmp_path):
    (tmp_path / "gated.yml").write_text("on: pull_request\njobs:\n  a:\n    runs-on: ubuntu-latest\n")
    (tmp_path / "not-gated.yml").write_text("on: workflow_dispatch\njobs:\n  a:\n    runs-on: ubuntu-latest\n")

    found = pr_triggered_workflows(tmp_path)

    assert [path.name for path, _ in found] == ["gated.yml"]
