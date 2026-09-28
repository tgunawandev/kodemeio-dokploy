#!/usr/bin/env python3
"""Validate synthetic/local Teracorp G5 onboarding evidence; never provisions accounts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REF = re.compile(r"^[A-Za-z][A-Za-z0-9:._/-]{2,127}$")
_GROUP = re.compile(r"^ak-[a-z0-9-]{2,80}$")
_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_MAX_BYTES = 262_144
_MAX_JSON_NESTING = 64
_MAX_EVENTS = 100
_PRIVILEGED = ("admin", "devops", "platform", "svc", "superuser")
_ROOT_KEYS = {"schema_version", "policy", "trigger", "events"}


class InputError(ValueError):
    """Input is invalid or fails a required G5 evidence gate."""


def _obj(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise InputError(f"{where} must be an object with exactly the required keys")
    return value


def _ref(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value) or "://" in value:
        raise InputError(f"{where} must be an opaque evidence reference")
    return value


def _utc(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise InputError(f"{where} must be a canonical UTC timestamp ending in Z")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise InputError(f"{where} must be a valid UTC timestamp") from exc
    return parsed


def _policy(value: Any) -> tuple[int | float, datetime, int]:
    policy = _obj(
        value,
        "policy",
        {
            "hours_threshold",
            "unit",
            "comparator",
            "measurement_window_weeks",
            "approved_by_ref",
            "approved_at_utc",
            "decision_ref",
        },
    )
    threshold = policy["hours_threshold"]
    if threshold is None:
        raise InputError("founder-hours threshold is unconfigured; onboarding is refused")
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold <= 0:
        raise InputError("policy.hours_threshold must be a positive founder-approved number")
    if policy["unit"] != "hours/week" or policy["comparator"] != "gt":
        raise InputError("policy must explicitly use hours/week and strict greater-than semantics")
    if type(policy["measurement_window_weeks"]) is not int or not 1 <= policy["measurement_window_weeks"] <= 52:
        raise InputError("policy.measurement_window_weeks must be a founder-approved integer in 1..52")
    _ref(policy["approved_by_ref"], "policy.approved_by_ref")
    _ref(policy["decision_ref"], "policy.decision_ref")
    approved_at = _utc(policy["approved_at_utc"], "policy.approved_at_utc")
    return threshold, approved_at, policy["measurement_window_weeks"]


def _trigger(
    value: Any, threshold: int | float, policy_approved_at: datetime, measurement_window_weeks: int
) -> datetime:
    trigger = _obj(
        value,
        "trigger",
        {
            "measured_hours_per_week",
            "period_start",
            "period_end",
            "source_evidence_ref",
            "reviewer_ref",
            "reviewed_at_utc",
        },
    )
    hours = trigger["measured_hours_per_week"]
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0:
        raise InputError("trigger.measured_hours_per_week must be non-negative numeric verified evidence")
    start = _utc(trigger["period_start"], "trigger.period_start")
    end = _utc(trigger["period_end"], "trigger.period_end")
    reviewed = _utc(trigger["reviewed_at_utc"], "trigger.reviewed_at_utc")
    if start >= end or reviewed < end or start < policy_approved_at:
        raise InputError("trigger dates must be ordered, measured after policy approval, and reviewed after the period")
    if (end - start).total_seconds() != measurement_window_weeks * 7 * 24 * 60 * 60:
        raise InputError("trigger period must exactly match the founder-approved measurement window")
    _ref(trigger["source_evidence_ref"], "trigger.source_evidence_ref")
    _ref(trigger["reviewer_ref"], "trigger.reviewer_ref")
    if hours <= threshold:
        raise InputError("founder-hours trigger has not been exceeded; onboarding is refused")
    return reviewed


def _joiner_chronology(
    event: dict[str, Any], where: str, trigger_reviewed: datetime, policy_approved: datetime
) -> None:
    """Onboard only after the trigger was reviewed, under a role mapping approved under the current policy."""
    occurred = _utc(event["occurred_at_utc"], f"{where}.occurred_at_utc")
    starts = _utc(event["access"]["starts_at_utc"], f"{where}.access.starts_at_utc")
    if occurred < trigger_reviewed or starts < trigger_reviewed:
        raise InputError(f"{where} joiner event and access start must not predate the founder-hours trigger review")
    if _utc(event["role_mapping"]["approved_at_utc"], f"{where}.role_mapping.approved_at_utc") < policy_approved:
        raise InputError(f"{where}.role_mapping approval must not predate the founder policy approval")


def _mapping(value: Any, approver_ref: str, occurred: datetime, access_starts: datetime) -> str:
    mapping = _obj(
        value,
        "role_mapping",
        {"authentik_group", "mapping_evidence_ref", "approved_by_ref", "approved_at_utc", "resource_scope_ref"},
    )
    group = mapping["authentik_group"]
    if not isinstance(group, str) or not _GROUP.fullmatch(group):
        raise InputError("role_mapping.authentik_group must be one exact Authentik group identifier")
    lowered = group.lower()
    if any(term in lowered for term in _PRIVILEGED):
        raise InputError("privileged or service groups are forbidden for contractor onboarding")
    for key in ("mapping_evidence_ref", "approved_by_ref", "resource_scope_ref"):
        _ref(mapping[key], f"role_mapping.{key}")
    if mapping["approved_by_ref"] != approver_ref:
        raise InputError("role_mapping.approved_by_ref must match the lifecycle event approver_ref")
    approved_at = _utc(mapping["approved_at_utc"], "role_mapping.approved_at_utc")
    if approved_at >= occurred or approved_at >= access_starts:
        raise InputError("role_mapping approval must precede the lifecycle event and access start")
    return mapping["mapping_evidence_ref"]


def _event(value: Any, index: int) -> None:
    where = f"events[{index}]"
    event = _obj(
        value,
        where,
        {
            "event_id",
            "action",
            "subject_ref",
            "occurred_at_utc",
            "operator_ref",
            "approver_ref",
            "verifier_ref",
            "role_mapping",
            "mfa",
            "mattermost",
            "access",
            "change",
            "revocation",
        },
    )
    action = event["action"]
    if not isinstance(action, str) or action not in {"joiner", "mover", "leaver"}:
        raise InputError(f"{where}.action must be joiner, mover, or leaver")
    for key in ("event_id", "subject_ref", "operator_ref", "approver_ref", "verifier_ref"):
        _ref(event[key], f"{where}.{key}")
    occurred = _utc(event["occurred_at_utc"], f"{where}.occurred_at_utc")

    mfa = _obj(event["mfa"], f"{where}.mfa", {"status", "evidence_ref", "verified_at_utc"})
    if mfa["status"] != "verified_enrolled":
        raise InputError(f"{where}.mfa must be verified_enrolled before granting access")
    _ref(mfa["evidence_ref"], f"{where}.mfa.evidence_ref")
    _utc(mfa["verified_at_utc"], f"{where}.mfa.verified_at_utc")

    mm = _obj(
        event["mattermost"],
        f"{where}.mattermost",
        {
            "team_ref",
            "private_channel_refs",
            "private_channels_verified",
            "privacy_evidence_ref",
            "membership_evidence_ref",
        },
    )
    _ref(mm["team_ref"], f"{where}.mattermost.team_ref")
    _ref(mm["membership_evidence_ref"], f"{where}.mattermost.membership_evidence_ref")
    channels = mm["private_channel_refs"]
    if type(channels) is not list or not channels:
        raise InputError(f"{where}.mattermost.private_channel_refs must list approved private channels")
    for channel in channels:
        _ref(channel, f"{where}.mattermost.private_channel_ref")
    if mm["private_channels_verified"] is not True:
        raise InputError(f"{where}.mattermost.private_channels_verified must be true")
    _ref(mm["privacy_evidence_ref"], f"{where}.mattermost.privacy_evidence_ref")

    access = _obj(event["access"], f"{where}.access", {"starts_at_utc", "expires_at_utc", "expiry_evidence_ref"})
    starts = _utc(access["starts_at_utc"], f"{where}.access.starts_at_utc")
    expires = _utc(access["expires_at_utc"], f"{where}.access.expires_at_utc")
    if starts >= expires:
        raise InputError(f"{where}.access expiry must be after access start")
    _ref(access["expiry_evidence_ref"], f"{where}.access.expiry_evidence_ref")
    mapping_evidence_ref = _mapping(event["role_mapping"], event["approver_ref"], occurred, starts)
    # Evidence references are claim-specific in this contract. Reusing the
    # role-mapping evidence for MFA or expiry would leave those claims ambiguous.
    if mapping_evidence_ref in {
        mfa["evidence_ref"],
        mm["privacy_evidence_ref"],
        mm["membership_evidence_ref"],
        access["expiry_evidence_ref"],
    }:
        raise InputError(f"{where}.role_mapping.mapping_evidence_ref must identify role-mapping evidence only")
    if event["action"] in {"joiner", "mover"} and not starts <= occurred < expires:
        raise InputError(f"{where}.access must be active at the joiner/mover event time")
    if _utc(mfa["verified_at_utc"], f"{where}.mfa.verified_at_utc") > starts:
        raise InputError(f"{where}.mfa must be verified before access starts")

    change = _obj(event["change"], f"{where}.change", {"old_scope_ref", "new_scope_ref", "approval_ref"})
    if event["action"] == "mover":
        for key in change:
            _ref(change[key], f"{where}.change.{key}")
        if change["old_scope_ref"] == change["new_scope_ref"]:
            raise InputError(f"{where}.change must identify a changed scope")
    elif any(change.values()):
        raise InputError(f"{where}.change must be empty for joiner/leaver events")

    revocation = _obj(
        event["revocation"],
        f"{where}.revocation",
        {"authentik", "mattermost_sessions", "mattermost_memberships", "owned_tokens", "evidence_ref"},
    )
    if event["action"] == "leaver":
        for surface in ("authentik", "mattermost_sessions", "mattermost_memberships", "owned_tokens"):
            evidence = _obj(
                revocation[surface],
                f"{where}.revocation.{surface}",
                {"status", "completed_at_utc", "evidence_ref", "not_applicable_reason_ref"},
            )
            status = evidence["status"]
            if surface in {"authentik", "mattermost_memberships"} and status != "revoked":
                raise InputError(f"{where}.revocation.{surface} must prove revocation")
            if status == "revoked":
                completed_at = _utc(evidence["completed_at_utc"], f"{where}.revocation.{surface}.completed_at_utc")
                if completed_at < _utc(event["occurred_at_utc"], f"{where}.occurred_at_utc"):
                    raise InputError(f"{where}.revocation.{surface} completion must not predate the leaver event")
                _ref(evidence["evidence_ref"], f"{where}.revocation.{surface}.evidence_ref")
                if evidence["not_applicable_reason_ref"] is not None:
                    raise InputError(f"{where}.revocation.{surface} cannot be both revoked and not applicable")
            elif status == "not_applicable":
                _ref(evidence["not_applicable_reason_ref"], f"{where}.revocation.{surface}.not_applicable_reason_ref")
                if evidence["completed_at_utc"] is not None or evidence["evidence_ref"] is not None:
                    raise InputError(f"{where}.revocation.{surface} not_applicable evidence must not claim a revoke")
            else:
                raise InputError(f"{where}.revocation.{surface} must prove revoked or evidence-backed not_applicable")
        _ref(revocation["evidence_ref"], f"{where}.revocation.evidence_ref")
    else:
        empty_surface = {
            "status": None,
            "completed_at_utc": None,
            "evidence_ref": None,
            "not_applicable_reason_ref": None,
        }
        expected_empty = {
            "authentik": empty_surface,
            "mattermost_sessions": empty_surface,
            "mattermost_memberships": empty_surface,
            "owned_tokens": empty_surface,
            "evidence_ref": None,
        }
        if revocation != expected_empty:
            raise InputError(f"{where}.revocation must be empty except for leaver events")


def validate_bundle(payload: Any) -> dict[str, Any]:
    """Fail closed unless founder trigger and each supplied synthetic lifecycle record are evidenced."""
    root = _obj(payload, "bundle", _ROOT_KEYS)
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("bundle.schema_version must be integer 1")
    events = root["events"]
    if type(events) is not list:
        raise InputError("bundle.events must be a list")
    trigger_reviewed = approved_at = None
    onboarding_requested = not events or any(type(item) is dict and item.get("action") == "joiner" for item in events)
    if onboarding_requested:
        threshold, approved_at, measurement_window_weeks = _policy(root["policy"])
        trigger_reviewed = _trigger(root["trigger"], threshold, approved_at, measurement_window_weeks)
    if not 1 <= len(events) <= _MAX_EVENTS:
        raise InputError(f"bundle.events must contain 1..{_MAX_EVENTS} complete synthetic lifecycle records")
    event_ids: set[str] = set()
    for index, item in enumerate(events):
        _event(item, index)
        if item["action"] == "joiner":
            if trigger_reviewed is None or approved_at is None:
                raise InputError("joiner events require a validated founder-hours trigger")
            _joiner_chronology(item, f"events[{index}]", trigger_reviewed, approved_at)
        if item["event_id"] in event_ids:
            raise InputError("event_id values must be unique")
        event_ids.add(item["event_id"])
    return {
        "schema_version": 1,
        "trigger_exceeded": onboarding_requested,
        "events_validated": len(events),
        "result": "evidence_contract_valid_only",
    }


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key")
        result[key] = value
    return result


def _check_json_nesting(raw: str) -> None:
    """Bound structural JSON nesting without counting brackets inside strings."""
    depth = 0
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_JSON_NESTING:
                raise InputError(f"input JSON nesting must not exceed {_MAX_JSON_NESTING} levels")
        elif char in "]}" and depth:
            depth -= 1


def _read_bounded(path: Path) -> bytes:
    """Read only enough bytes to accept a valid input or detect an oversized one."""
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise InputError(f"input must not exceed {_MAX_BYTES} bytes")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="explicit synthetic/local G5 evidence JSON")
    args = parser.parse_args(argv)
    try:
        raw = _read_bounded(args.bundle)
        text = raw.decode("utf-8")
        _check_json_nesting(text)
        try:
            payload = json.loads(text, object_pairs_hook=_pairs)
        except json.JSONDecodeError:
            raise
        except InputError:
            raise
        except (RecursionError, ValueError) as exc:
            raise InputError("input JSON exceeds parser safety limits") from exc
        print(json.dumps(validate_bundle(payload), sort_keys=True))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InputError) as exc:
        print(f"teracorp_contractor_readiness: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
