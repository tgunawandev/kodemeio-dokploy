from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/teracorp_autonomy_evidence.py"
SPEC = importlib.util.spec_from_file_location("teracorp_autonomy_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUTONOMY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTONOMY)
POLICY = AUTONOMY._load_policy()
SAMPLE = json.loads((SCRIPT.parents[1] / "autonomy/evidence.synthetic.json").read_text(encoding="utf-8"))


def test_green_synthetic_package_only_produces_unverified_manual_review_candidate() -> None:
    result = AUTONOMY.evaluate(copy.deepcopy(SAMPLE), POLICY)
    assert result["status"] == "candidate_for_manual_review_unverified"
    assert result["evidence_trust"] == "caller_asserted_unverified"
    assert result["runtime_policy_changed"] is False
    assert "separate signed policy change" in result["warning"]


@pytest.mark.parametrize("action_class", ["production_deploy", "money_movement", "destructive_delete"])
def test_always_human_class_refuses_even_with_caller_supplied_good_evidence(action_class: str) -> None:
    evidence = copy.deepcopy(SAMPLE)
    evidence["action_class"] = action_class
    result = AUTONOMY.evaluate(evidence, POLICY)
    assert result["status"] == "always_human_refused"
    assert result["runtime_policy_changed"] is False


def test_always_human_policy_addition_overrides_draft_candidate() -> None:
    policy = copy.deepcopy(POLICY)
    policy["always_human"].append("draft")
    with pytest.raises(AUTONOMY.InputError, match="always_human contract changed"):
        AUTONOMY.evaluate(copy.deepcopy(SAMPLE), policy)


def test_policy_cannot_remove_always_human_classes_or_weaken_risk_mappings() -> None:
    policy = copy.deepcopy(POLICY)
    policy["always_human"].remove("money_movement")
    with pytest.raises(AUTONOMY.InputError, match="always_human contract changed"):
        AUTONOMY.evaluate(copy.deepcopy(SAMPLE), policy)
    policy = copy.deepcopy(POLICY)
    policy["risk_mapping"]["financial"] = "auto-approvable"
    with pytest.raises(AUTONOMY.InputError, match="risk mapping changed"):
        AUTONOMY.evaluate(copy.deepcopy(SAMPLE), policy)


def test_checked_in_policy_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(AUTONOMY._POLICY.read_text(encoding="utf-8") + "\nversion: 99\n", encoding="utf-8")
    with pytest.raises(AUTONOMY.InputError, match="YAML is invalid"):
        AUTONOMY._load_policy(path)


def test_policy_rule_drift_fails_closed() -> None:
    policy = copy.deepcopy(POLICY)
    policy["risk_mapping"]["draft"] = "auto-approvable with good score"
    with pytest.raises(AUTONOMY.InputError, match="risk mapping changed"):
        AUTONOMY.evaluate(copy.deepcopy(SAMPLE), policy)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value["runs"].pop(), "too_few_green_runs"),
        (lambda value: value["runs"][0].update(passed_count=19), "run_not_green"),
        (lambda value: value["runs"][1].update(mutation_checks_passed=1), "mutation_coverage_incomplete"),
        (lambda value: value.update(incident_count_90d=1), "incident_threshold_exceeded"),
        (lambda value: value["thresholds"].update(minimum_green_runs=0), "minimum_green_runs"),
        (
            lambda value: (value["runs"].pop(), value["thresholds"].update(maximum_run_age_days=2)),
            "latest_run_stale",
        ),
    ],
    ids=["too-few-runs", "failed-eval", "mutation-gap", "incident", "zero-threshold", "stale"],
)
def test_insufficient_evidence_never_becomes_candidate(mutate, reason: str) -> None:
    evidence = copy.deepcopy(SAMPLE)
    mutate(evidence)
    if reason == "minimum_green_runs":
        with pytest.raises(AUTONOMY.InputError, match=reason):
            AUTONOMY.evaluate(evidence, POLICY)
        return
    result = AUTONOMY.evaluate(evidence, POLICY)
    assert result["status"] == "evidence_insufficient"
    assert reason in result["reasons"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["runs"][0].update(observed_on="2026-09-29"),
        lambda value: value["runs"][1].update(run_id=value["runs"][0]["run_id"]),
        lambda value: value["runs"][1].update(observed_on=value["runs"][0]["observed_on"]),
        lambda value: value["runs"][0].update(extra="unexpected"),
        lambda value: value["runs"][0].update(passed_count=True),
    ],
    ids=["future", "duplicate-id", "duplicate-date", "unknown-field", "bool-not-int"],
)
def test_malformed_or_future_runs_refuse(mutate) -> None:
    evidence = copy.deepcopy(SAMPLE)
    mutate(evidence)
    with pytest.raises(AUTONOMY.InputError):
        AUTONOMY.evaluate(evidence, POLICY)


def test_non_draft_action_class_cannot_enter_manual_review() -> None:
    evidence = copy.deepcopy(SAMPLE)
    evidence["action_class"] = "financial"
    assert AUTONOMY.evaluate(evidence, POLICY)["status"] == "evidence_insufficient"


def test_cli_output_is_deterministic_and_uses_only_explicit_input(tmp_path, capsys) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    assert AUTONOMY.main([str(path)]) == 0
    first = capsys.readouterr().out
    assert AUTONOMY.main([str(path)]) == 0
    assert capsys.readouterr().out == first
    assert json.loads(first)["evidence_trust"] == "caller_asserted_unverified"


def test_cli_rejects_oversized_input(tmp_path, capsys) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (AUTONOMY._MAX_INPUT_BYTES + 1))
    assert AUTONOMY.main([str(path)]) == 2
    assert "exceeds" in capsys.readouterr().err


def test_cli_rejects_huge_json_integer_without_traceback(tmp_path, capsys) -> None:
    path = tmp_path / "huge-integer.json"
    path.write_text('{"incident_count_90d":' + "9" * 5000 + "}", encoding="utf-8")
    assert AUTONOMY.main([str(path)]) == 2
    error = capsys.readouterr().err
    assert "numeric bound" in error
    assert "Traceback" not in error


def test_script_has_no_network_database_or_process_execution_dependencies() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})
