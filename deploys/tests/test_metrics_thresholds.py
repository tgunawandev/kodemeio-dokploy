"""P4 (observability with redaction): the committed thresholds, the jobs that
enforce them, and the redaction contract those jobs mirror.

Three things are joined here, and each join is a way this slice could rot
silently:

1. `ops/monitoring/metrics/thresholds.yaml` vs the defaults INSIDE
   `kodemeio-skills docker/lib/metrics.py` -- the numbers the jobs actually
   apply when the env override is unset. A threshold that stops matching its
   job is an alarm nobody gets.
2. thresholds.yaml's class table vs the job scripts' `kod_metrics_pipeline
   <class> <source> <reader>` line, and vs the schedules in
   `deploys/instances/production/kod-infra-kctl.yaml` -- a renamed class, job
   or schedule is a snapshot nothing reads, or a `jobrun` that 404s inside
   Dokploy's scheduler, which has no failure notification.
3. metrics.py's MARKERS/PATTERNS vs `contracts/observability/redaction.v1.yaml`
   -- the same drift check Wave 0 T12 applies to the Odoo Sentry scrubber. The
   snapshot writer is allowed to be STRICTER than the contract (it adds an
   `sk-...` key pattern, and over-redaction is the safe direction); it is never
   allowed to be missing a marker or a pattern the contract declares.

All three cross-repo checks skip when kodemeio-skills is not checked out
beside this repo (the T12 pattern), and fail in CI when that repo is listed in
CI_GATES_REQUIRED_SIBLINGS.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import test_ci_gates
import yaml

REPO = Path(__file__).resolve().parents[2]
THRESHOLDS = REPO / "ops" / "monitoring" / "metrics" / "thresholds.yaml"
CONTRACT = REPO / "contracts" / "observability" / "redaction.v1.yaml"
MANIFEST = REPO / "deploys" / "instances" / "production" / "kod-infra-kctl.yaml"

SIBLING = "kodemeio-skills"
SKILLS = test_ci_gates.WORKSPACE_ROOT / SIBLING
METRICS_PY = SKILLS / "docker" / "lib" / "metrics.py"
JOBS_DIR = SKILLS / "docker" / "jobs"

# The env var each threshold is overridable through, and the constant that
# holds its default inside metrics.py.
CODE_CONSTANTS = {
    "token-cost": ("METRICS_TOKEN_WARN_PCT", "DEFAULT_TOKEN_WARN_PCT", "warn_pct"),
    "queue-lag": ("METRICS_QUEUE_LAG_S", "DEFAULT_QUEUE_LAG_S", "lag_seconds"),
}


@pytest.fixture(scope="module")
def thresholds() -> dict:
    return yaml.safe_load(THRESHOLDS.read_text())


def _require_skills() -> None:
    if METRICS_PY.is_file():
        return
    verdict = test_ci_gates.missing_sibling_verdict(SIBLING, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI)
    if verdict == "fail":
        pytest.fail(f"{SIBLING} is required in CI but {METRICS_PY} does not exist")
    pytest.skip(f"{SIBLING} not checked out at {SKILLS} -- cross-repo checks skipped")


def _metrics_constants() -> dict:
    """Assignments at module level of metrics.py, without importing it."""
    _require_skills()
    out: dict[str, object] = {}
    for node in ast.parse(METRICS_PY.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"MARKERS", "PATTERNS"} | {const for _, const, _ in CODE_CONSTANTS.values()}:
                out[name] = ast.literal_eval(node.value)
    return out


# ---------------------------------------------------------------------------
# 1. the committed thresholds match the code
# ---------------------------------------------------------------------------


def test_thresholds_shape(thresholds):
    assert thresholds["version"] == 1
    classes = thresholds["classes"]
    assert set(classes) == {"token-cost", "queue-lag", "action-outcome", "product-funnel"}
    for name, spec in classes.items():
        assert spec["job"], f"{name}: no job"
        assert spec["source"], f"{name}: no source"
        assert spec["snapshot"] == f"{name}.json", f"{name}: snapshot name must be <class>.json"
        assert isinstance(spec["thresholds"], dict), f"{name}: thresholds must be a mapping"
        assert spec["measured_against"], f"{name}: say what the number is measured against"
    assert set(classes["token-cost"]["thresholds"]) == {"warn_pct"}
    assert set(classes["queue-lag"]["thresholds"]) == {"lag_seconds"}
    # A class with no threshold states that, rather than omitting the key.
    assert classes["action-outcome"]["thresholds"] == {}
    assert classes["product-funnel"]["thresholds"] == {}
    assert thresholds["healthchecks"]["env_vars"] == [
        "HC_KOD_METRICS_TOKEN_COST",
        "HC_KOD_METRICS_QUEUE_LAG",
        "HC_KOD_METRICS_OUTCOME",
        "HC_KOD_METRICS_FUNNEL",
    ]


def test_thresholds_match_the_code_defaults(thresholds):
    constants = _metrics_constants()
    for name, (env_var, code_const, yaml_key) in CODE_CONSTANTS.items():
        spec = thresholds["classes"][name]
        assert spec["env_override"] == env_var, f"{name}: env override drifted"
        assert spec["default_in_code"] == code_const, f"{name}: constant name drifted"
        assert float(spec["thresholds"][yaml_key]) == float(constants[code_const]), (
            f"{name}: thresholds.yaml says {yaml_key}="
            f"{spec['thresholds'][yaml_key]}, metrics.py says {code_const}="
            f"{constants[code_const]} -- change both, or the job enforces a number nobody recorded"
        )


def test_threshold_defaults_are_sane(thresholds):
    assert thresholds["classes"]["token-cost"]["thresholds"]["warn_pct"] < 100, (
        "a warn threshold at or above 100 % can only fire after LiteLLM's own budget already blocks the key"
    )
    assert thresholds["classes"]["queue-lag"]["thresholds"]["lag_seconds"] >= 600, (
        "a lag threshold under 10 minutes fires on ordinary work"
    )


# ---------------------------------------------------------------------------
# 2. the jobs and the schedules agree with the table
# ---------------------------------------------------------------------------

PIPELINE = re.compile(r"^kod_metrics_pipeline\s+(\S+)\s+(\S+)\s+(\S+)\s+", re.MULTILINE)


def _job_pipelines() -> dict[str, tuple[str, str]]:
    """job name -> (class, source), read from each job script's pipeline call."""
    _require_skills()
    found = {}
    for path in sorted(JOBS_DIR.glob("kod-metrics-*.sh")):
        match = PIPELINE.search(path.read_text())
        assert match, f"{path.name}: no kod_metrics_pipeline call"
        found[path.stem] = (match.group(1), match.group(2))
    return found


def test_every_job_runs_a_class_from_the_table(thresholds):
    pipelines = _job_pipelines()
    assert set(pipelines) == {spec["job"] for spec in thresholds["classes"].values()}, (
        f"jobs and thresholds.yaml disagree: {sorted(pipelines)}"
    )
    for job, (cls, source) in pipelines.items():
        spec = thresholds["classes"][cls]
        assert spec["job"] == job, f"{job} runs class {cls}, which the table assigns to {spec['job']}"
        assert spec["source"] == source, f"{job}: table says source {spec['source']}, job uses {source}"


def test_every_class_is_scheduled_and_disabled(thresholds):
    schedules = {s["name"]: s for s in yaml.safe_load(MANIFEST.read_text())["schedules"]}
    for spec in thresholds["classes"].values():
        job = spec["job"]
        assert job in schedules, f"{job} has no schedule in kod-infra-kctl.yaml"
        assert schedules[job]["command"] == f"jobrun {job}", (
            f"{job}: command must be a bare `jobrun <name>` (Dokploy does not escape metacharacters)"
        )
        assert schedules[job]["enabled"] is False, (
            f"{job}: P4 ships every metrics schedule disabled -- see the manifest's own gate comment"
        )
    for job in schedules:
        if job.startswith("kod-metrics-"):
            assert job in {spec["job"] for spec in thresholds["classes"].values()}, (
                f"{job} is scheduled but is not a class in thresholds.yaml"
            )


# ---------------------------------------------------------------------------
# 3. the snapshot writer mirrors the redaction contract
# ---------------------------------------------------------------------------


def test_metrics_py_keeps_the_contract_markers_and_patterns():
    constants = _metrics_constants()
    contract = yaml.safe_load(CONTRACT.read_text())
    markers = constants["MARKERS"]
    patterns = constants["PATTERNS"]
    for marker in contract["synthetic_markers"]:
        assert marker in markers, f"metrics.py lost the contract marker {marker!r} -- snapshots would carry it"
    missing = set(contract["redact_value_patterns"]) - set(patterns)
    assert not missing, f"metrics.py is missing contract pattern(s): {sorted(missing)}"
    for name, pattern in patterns.items():
        assert isinstance(pattern, str) and pattern, f"{name}: empty pattern"
        re.compile(pattern)  # must compile
    # Stricter than the contract is allowed (over-redaction is the safe
    # direction); looser is not. Every contract pattern must be byte-identical
    # OR obviously stricter -- the two we keep verbatim are the ones with a
    # value the tests plant.
    for name, pattern in contract["redact_value_patterns"].items():
        assert patterns[name] == pattern, f"{name}: metrics.py must not loosen the contract's pattern"
