#!/usr/bin/env python3
"""Build an offline, unverified manual-review packet for a low-risk action class.

This utility never changes policy or grants autonomy. Evaluation evidence and thresholds are
caller-supplied claims; a passing report is not a runtime authorization.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

_MAX_INPUT_BYTES = 1_048_576
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_ROOT = Path(__file__).resolve().parents[2]
_POLICY = _ROOT / "contracts/approvals/policy.v1.yaml"
_AUTONOMY = _ROOT / "contracts/approvals/autonomy.v1.yaml"
_AUTONOMY_KEYS = {
    "version",
    "policy_version",
    "already_autonomous",
    "promotable",
    "never_promotable",
    "max_grant_days",
    "trust_roots",
}
_MAX_JSON_INT = 1_000_000_000
_EXPECTED_ALWAYS_HUMAN = [
    "production_deploy",
    "schema_migration",
    "dns_security_permission",
    "destructive_delete",
    "money_movement",
]
_EXPECTED_RISK_MAPPING = {
    "draft": "auto-approvable when the profile sets auto_approve_draft",
    "operational": "human approver, distinct from requester when require_distinct_approver",
    "financial": "human approver; never auto",
    "admin": "human approver; never auto",
}


class InputError(ValueError):
    """Policy or evidence violates the offline report contract."""


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise InputError(f"{where} must be an object")
    if set(value) != keys:
        raise InputError(f"{where} keys must be exactly {sorted(keys)}")
    return value


def _parse_date(value: Any, where: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise InputError(f"{where} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{where} must be a valid date") from exc
    if parsed.isoformat() != value:
        raise InputError(f"{where} must be canonical YYYY-MM-DD")
    return parsed


def _int(value: Any, where: str, *, minimum: int, maximum: int = 1_000_000_000) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise InputError(f"{where} must be an integer between {minimum} and {maximum}")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bounded_json_int(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > len(str(_MAX_JSON_INT)):
        raise InputError("JSON integer token exceeds the supported numeric bound")
    value = int(token)
    if value > _MAX_JSON_INT or value < -_MAX_JSON_INT:
        raise InputError("JSON integer token exceeds the supported numeric bound")
    return value


def _validate_policy(policy: Any) -> dict[str, Any]:
    if type(policy) is not dict or set(policy) != {"version", "always_human", "risk_mapping"}:
        raise InputError("approval policy must be an object")
    if type(policy["version"]) is not int or policy["version"] != 1:
        raise InputError("approval policy version changed; refuse pending contract update")
    always_human = policy.get("always_human")
    mapping = policy.get("risk_mapping")
    if always_human != _EXPECTED_ALWAYS_HUMAN:
        raise InputError("approval policy always_human contract changed; refuse pending review")
    if mapping != _EXPECTED_RISK_MAPPING:
        raise InputError("approval policy risk mapping changed; refuse pending review")
    return {"version": 1, "always_human": list(always_human), "risk_mapping": dict(mapping)}


def _class_list(value: Any, where: str) -> list[str]:
    if type(value) is not list or len(value) > 32 or len(set(value)) != len(value):
        raise InputError(f"autonomy contract {where} must be a unique list")
    for item in value:
        if not isinstance(item, str) or not _ID.fullmatch(item):
            raise InputError(f"autonomy contract {where} entries must be safe identifiers")
    return list(value)


def _validate_autonomy_contract(contract: Any, policy: dict[str, Any]) -> dict[str, Any]:
    """Check the promotion contract against policy.v1 so it can never widen a forbidden class."""
    policy = _validate_policy(policy)
    if type(contract) is not dict or set(contract) != _AUTONOMY_KEYS:
        raise InputError("autonomy contract must be an object with the v1 keys")
    if type(contract["version"]) is not int or contract["version"] != 1:
        raise InputError("autonomy contract version changed; refuse pending contract update")
    if type(contract["policy_version"]) is not int or contract["policy_version"] != policy["version"]:
        raise InputError("autonomy contract policy_version does not match the approval policy")
    already = _class_list(contract["already_autonomous"], "already_autonomous")
    promotable = _class_list(contract["promotable"], "promotable")
    never = _class_list(contract["never_promotable"], "never_promotable")
    mapping = policy["risk_mapping"]
    for name in promotable:
        if name in policy["always_human"]:
            raise InputError("autonomy contract promotable overlaps always_human")
        if name in never:
            raise InputError("autonomy contract promotable overlaps never_promotable")
        if name in already:
            raise InputError("autonomy contract promotable overlaps already_autonomous")
        if name not in mapping:
            raise InputError("autonomy contract promotable class is not in the policy risk_mapping")
    for name, rule in mapping.items():
        if "never auto" in rule and name not in never:
            raise InputError("autonomy contract must list every 'never auto' class as never_promotable")
        if rule.startswith("auto-approvable") and name not in already:
            raise InputError("autonomy contract must list every auto-approvable class as already_autonomous")
    for name in already:
        if not mapping.get(name, "").startswith("auto-approvable"):
            raise InputError("autonomy contract already_autonomous class is not auto-approvable in the policy")
    max_days = _int(contract["max_grant_days"], "autonomy contract max_grant_days", minimum=1, maximum=366)
    roots = contract["trust_roots"]
    if type(roots) is not list or len(roots) > 8:
        raise InputError("autonomy contract trust_roots must be a list of at most 8 keys")
    checked_roots = []
    for index, root in enumerate(roots):
        root = _object(root, f"autonomy contract trust_roots[{index}]", {"key_id", "algorithm", "public_key"})
        if not isinstance(root["key_id"], str) or not _ID.fullmatch(root["key_id"]):
            raise InputError("autonomy contract trust root key_id must be a safe identifier")
        if root["algorithm"] != "ed25519" or not isinstance(root["public_key"], str):
            raise InputError("autonomy contract trust roots must be ed25519 public keys")
        checked_roots.append(dict(root))
    if len({root["key_id"] for root in checked_roots}) != len(checked_roots):
        raise InputError("autonomy contract trust root key_ids must be unique")
    return {
        "version": 1,
        "policy_version": policy["version"],
        "already_autonomous": already,
        "promotable": promotable,
        "never_promotable": never,
        "max_grant_days": max_days,
        "trust_roots": checked_roots,
    }


def _read_yaml(path: Path, what: str) -> Any:
    try:
        import yaml

        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
            result: dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in result:
                    raise yaml.constructor.ConstructorError("duplicate YAML mapping key")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result

        UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

        with path.open("rb") as stream:
            raw = stream.read(65_537)
        if len(raw) > 65_536:
            raise InputError(f"{what} exceeds 65536 bytes")
    except OSError as exc:
        raise InputError(f"{what} cannot be read") from exc
    except ImportError as exc:
        raise InputError(f"PyYAML is required to read the checked-in {what}") from exc
    try:
        return yaml.load(raw, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise InputError(f"{what} YAML is invalid") from exc


def _load_policy(path: Path = _POLICY) -> dict[str, Any]:
    return _validate_policy(_read_yaml(path, "approval policy"))


def _load_autonomy_contract(path: Path = _AUTONOMY, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    return _validate_autonomy_contract(_read_yaml(path, "autonomy contract"), policy or _load_policy())


def evaluate(evidence: Any, policy: dict[str, Any], autonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = _validate_policy(policy)
    autonomy = (
        _validate_autonomy_contract(autonomy, policy)
        if autonomy is not None
        else _load_autonomy_contract(policy=policy)
    )
    package = _object(
        evidence,
        "evidence",
        {"schema_version", "action_class", "as_of", "thresholds", "incident_count_90d", "runs"},
    )
    if type(package["schema_version"]) is not int or package["schema_version"] != 1:
        raise InputError("evidence.schema_version must be integer 1")
    action_class = package["action_class"]
    if not isinstance(action_class, str) or not _ID.fullmatch(action_class):
        raise InputError("evidence.action_class must be a safe lowercase identifier")
    as_of = _parse_date(package["as_of"], "evidence.as_of")
    always_human = policy["always_human"]
    if action_class in always_human:
        return {
            "action_class": action_class,
            "as_of": as_of.isoformat(),
            "status": "always_human_refused",
            "evidence_trust": "caller_asserted_unverified",
            "runtime_policy_changed": False,
        }
    if action_class in autonomy["already_autonomous"]:
        return {
            "action_class": action_class,
            "as_of": as_of.isoformat(),
            "status": "already_autonomous_noop",
            "reason": "class is already auto-approvable; promotion would change nothing",
            "evidence_trust": "caller_asserted_unverified",
            "runtime_policy_changed": False,
        }
    if action_class not in autonomy["promotable"]:
        return {
            "action_class": action_class,
            "as_of": as_of.isoformat(),
            "status": "not_promotable_refused",
            "reason": "only classes in the versioned autonomy.v1 promotable list can enter manual review",
            "evidence_trust": "caller_asserted_unverified",
            "runtime_policy_changed": False,
        }

    thresholds = _object(
        package["thresholds"],
        "evidence.thresholds",
        {
            "minimum_green_runs",
            "minimum_cases_per_run",
            "minimum_mutation_checks_per_run",
            "maximum_incidents_90d",
            "maximum_run_age_days",
        },
    )
    min_runs = _int(thresholds["minimum_green_runs"], "thresholds.minimum_green_runs", minimum=1)
    min_cases = _int(thresholds["minimum_cases_per_run"], "thresholds.minimum_cases_per_run", minimum=1)
    min_mutations = _int(
        thresholds["minimum_mutation_checks_per_run"], "thresholds.minimum_mutation_checks_per_run", minimum=1
    )
    max_incidents = _int(thresholds["maximum_incidents_90d"], "thresholds.maximum_incidents_90d", minimum=0)
    max_age = _int(thresholds["maximum_run_age_days"], "thresholds.maximum_run_age_days", minimum=1)
    incidents = _int(package["incident_count_90d"], "evidence.incident_count_90d", minimum=0)
    runs = package["runs"]
    if type(runs) is not list or len(runs) > 100:
        raise InputError("evidence.runs must be an array of at most 100 runs")

    reasons: list[str] = []
    run_ids: set[str] = set()
    previous_date: date | None = None
    if len(runs) < min_runs:
        reasons.append("too_few_green_runs")
    for index, value in enumerate(runs):
        run = _object(
            value,
            f"evidence.runs[{index}]",
            {"run_id", "observed_on", "case_count", "passed_count", "mutation_check_count", "mutation_checks_passed"},
        )
        run_id = run["run_id"]
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id) or run_id in run_ids:
            raise InputError(f"evidence.runs[{index}].run_id must be unique and safe")
        run_ids.add(run_id)
        observed = _parse_date(run["observed_on"], f"evidence.runs[{index}].observed_on")
        if observed > as_of:
            raise InputError(f"evidence.runs[{index}] is after as_of")
        if previous_date is not None and observed <= previous_date:
            raise InputError("evidence.runs must be in strictly increasing date order")
        previous_date = observed
        cases = _int(run["case_count"], f"evidence.runs[{index}].case_count", minimum=0)
        passed = _int(run["passed_count"], f"evidence.runs[{index}].passed_count", minimum=0)
        mutation_count = _int(run["mutation_check_count"], f"evidence.runs[{index}].mutation_check_count", minimum=0)
        mutation_passed = _int(
            run["mutation_checks_passed"], f"evidence.runs[{index}].mutation_checks_passed", minimum=0
        )
        if passed > cases or mutation_passed > mutation_count:
            raise InputError(f"evidence.runs[{index}] passed counts exceed totals")
        if cases < min_cases:
            reasons.append("run_below_case_threshold")
        if passed != cases:
            reasons.append("run_not_green")
        if mutation_count < min_mutations or mutation_passed != mutation_count:
            reasons.append("mutation_coverage_incomplete")
    if previous_date is not None and (as_of - previous_date).days > max_age:
        reasons.append("latest_run_stale")
    if incidents > max_incidents:
        reasons.append("incident_threshold_exceeded")

    status = "evidence_insufficient" if reasons else "candidate_for_manual_review_unverified"
    result: dict[str, Any] = {
        "action_class": action_class,
        "as_of": as_of.isoformat(),
        "status": status,
        "evidence_trust": "caller_asserted_unverified",
        "runtime_policy_changed": False,
        "thresholds": thresholds,
        "run_count": len(runs),
        "incident_count_90d": incidents,
    }
    if reasons:
        result["reasons"] = sorted(set(reasons))
    else:
        result["warning"] = (
            "unverified inputs; founder review and a separate signed policy change "
            "(founder-signed autonomy-grant.v1) are required"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="explicit local JSON evidence package")
    args = parser.parse_args(argv)
    try:
        with args.evidence.open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise InputError(f"evidence input exceeds {_MAX_INPUT_BYTES} bytes")
        evidence = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_bounded_json_int,
        )
        result = evaluate(evidence, _load_policy())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InputError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
