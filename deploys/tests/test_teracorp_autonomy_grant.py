from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SCRIPTS = Path(__file__).resolve().parents[2] / "ops/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GRANT = _load("teracorp_autonomy_grant")
EVIDENCE = _load("teracorp_autonomy_evidence")

# Synthetic keys only: deterministic seeds that are not, and never become, founder keys.
FOUNDER_SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
POLICY = EVIDENCE._load_policy()
SAMPLE_EVIDENCE = json.loads((SCRIPTS.parent / "autonomy/evidence.synthetic.json").read_text(encoding="utf-8"))


def public_b64(seed: bytes) -> str:
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def contract(*roots: tuple[str, bytes]) -> dict:
    base = copy.deepcopy(EVIDENCE._load_autonomy_contract())
    base["trust_roots"] = [
        {"key_id": key_id, "algorithm": "ed25519", "public_key": public_b64(seed)} for key_id, seed in roots
    ] or [{"key_id": "synthetic-founder", "algorithm": "ed25519", "public_key": public_b64(FOUNDER_SEED)}]
    return base


def unsigned(**overrides) -> dict:
    grant = {
        "schema_version": 1,
        "grant_id": "grant-operational-001",
        "action_class": "operational",
        "policy_version": 1,
        "approver_role": "founder",
        "evidence_sha256": GRANT.evidence_digest(SAMPLE_EVIDENCE),
        "scope": {"profiles": ["teracorp-ops"]},
        "issued_at": "2026-09-28T00:00:00Z",
        "expires_at": "2026-10-28T00:00:00Z",
    }
    grant.update(overrides)
    return grant


def signed(seed: bytes = FOUNDER_SEED, key_id: str = "synthetic-founder", **overrides) -> dict:
    return GRANT.sign_grant(unsigned(**overrides), seed, key_id)


def verify(grant: dict, *, trust: dict | None = None, now: datetime = NOW):
    return GRANT.verify_grant(grant, POLICY, trust or contract(), now=now)


def test_founder_signed_grant_for_a_promotable_class_verifies() -> None:
    verified = verify(signed())
    assert verified.action_class == "operational"
    assert verified.profiles == ("teracorp-ops",)
    assert verified.key_id == "synthetic-founder"


def test_unsigned_grant_is_refused() -> None:
    with pytest.raises(GRANT.GrantRefused, match="^unsigned$"):
        verify(unsigned())


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_class", "operational-x"),
        ("expires_at", "2026-12-28T00:00:00Z"),
        ("evidence_sha256", "0" * 64),
        ("scope", {"profiles": ["teracorp-ops", "every-profile"]}),
        ("grant_id", "grant-operational-002"),
    ],
)
def test_tampered_grant_is_refused(field: str, value) -> None:
    grant = signed()
    grant[field] = value
    with pytest.raises(GRANT.GrantRefused, match="^signature_invalid$"):
        verify(grant)


def test_corrupted_signature_bytes_are_refused() -> None:
    grant = signed()
    raw = bytearray(base64.b64decode(grant["signature"]["value"]))
    raw[0] ^= 0x01
    grant["signature"]["value"] = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(GRANT.GrantRefused, match="^signature_invalid$"):
        verify(grant)


def test_wrong_key_is_refused_even_under_a_trusted_key_id() -> None:
    with pytest.raises(GRANT.GrantRefused, match="^signature_invalid$"):
        verify(signed(seed=OTHER_SEED))
    with pytest.raises(GRANT.GrantRefused, match="^unknown_key$"):
        verify(signed(seed=OTHER_SEED, key_id="someone-else"))


def test_empty_trust_root_refuses_every_grant() -> None:
    empty = copy.deepcopy(contract())
    empty["trust_roots"] = []
    with pytest.raises(GRANT.GrantRefused, match="^unknown_key$"):
        GRANT.verify_grant(signed(), POLICY, empty, now=NOW)


def test_checked_in_contract_has_no_trust_root_so_nothing_is_promoted_yet() -> None:
    runtime = GRANT.AutonomyRuntime(POLICY, grants=[signed()], clock=lambda: NOW)
    assert runtime.refused == [("grant-operational-001", "unknown_key")]
    assert runtime.decide("operational", "teracorp-ops").auto_approve is False


@pytest.mark.parametrize(
    "now,code",
    [
        (datetime(2026, 10, 28, 0, 0, tzinfo=UTC), "expired"),
        (datetime(2026, 11, 1, tzinfo=UTC), "expired"),
        (datetime(2026, 9, 27, 23, 59, tzinfo=UTC), "not_yet_valid"),
    ],
)
def test_expired_or_not_yet_valid_grant_is_refused(now: datetime, code: str) -> None:
    with pytest.raises(GRANT.GrantRefused, match=f"^{code}$"):
        verify(signed(), now=now)


def test_grant_validity_longer_than_the_contract_maximum_is_refused() -> None:
    with pytest.raises(GRANT.GrantRefused, match="^validity_too_long$"):
        verify(signed(expires_at="2027-01-01T00:00:00Z"))


@pytest.mark.parametrize(
    "action_class",
    ["production_deploy", "schema_migration", "dns_security_permission", "destructive_delete", "money_movement"],
)
def test_validly_signed_always_human_grant_is_refused(action_class: str) -> None:
    with pytest.raises(GRANT.GrantRefused, match="^always_human_refused$"):
        verify(signed(action_class=action_class))


@pytest.mark.parametrize(
    "action_class,code",
    [
        ("financial", "not_promotable"),
        ("admin", "not_promotable"),
        ("mystery", "not_promotable"),
        ("draft", "already_autonomous_noop"),
    ],
)
def test_validly_signed_grant_for_a_non_promotable_class_is_refused(action_class: str, code: str) -> None:
    with pytest.raises(GRANT.GrantRefused, match=f"^{code}$"):
        verify(signed(action_class=action_class))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.update(approver_role="operator"),
        lambda g: g.update(policy_version=2),
        lambda g: g.update(extra="field"),
        lambda g: g.update(scope={"profiles": []}),
        lambda g: g.update(issued_at="2026-09-28"),
        lambda g: g.update(schema_version=True),
    ],
)
def test_malformed_grant_is_refused_before_signature_checks(mutate) -> None:
    grant = unsigned()
    mutate(grant)
    with pytest.raises(GRANT.GrantRefused, match="^(grant_invalid|policy_version_mismatch)$"):
        verify(GRANT.sign_grant(grant, FOUNDER_SEED, "synthetic-founder"))


def test_valid_grant_promotes_exactly_one_class_in_one_scope() -> None:
    trust = contract()
    runtime = GRANT.AutonomyRuntime(POLICY, trust, grants=[signed()], clock=lambda: NOW)
    assert runtime.refused == []
    promoted = runtime.decide("operational", "teracorp-ops")
    assert promoted.auto_approve is True
    assert promoted.reason == "verified_grant"
    assert promoted.grant_id == "grant-operational-001"
    assert runtime.decide("operational", "other-profile").auto_approve is False
    for action_class in ["financial", "admin", "mystery", *POLICY["always_human"]]:
        assert runtime.decide(action_class, "teracorp-ops").auto_approve is False
    assert runtime.decide("draft", "teracorp-ops").auto_approve is False
    assert runtime.decide("draft", "teracorp-ops", auto_approve_draft=True).auto_approve is True
    before = GRANT.AutonomyRuntime(POLICY, trust, grants=[], clock=lambda: NOW)
    changed = [
        name
        for name in ["draft", "operational", "financial", "admin", *POLICY["always_human"]]
        if before.decide(name, "teracorp-ops").auto_approve != runtime.decide(name, "teracorp-ops").auto_approve
    ]
    assert changed == ["operational"]


def test_runtime_ignores_refused_grants_and_always_refuses_always_human() -> None:
    trust = contract()
    tampered = signed()
    tampered["scope"] = {"profiles": ["teracorp-ops", "everything"]}
    runtime = GRANT.AutonomyRuntime(
        POLICY,
        trust,
        grants=[unsigned(), tampered, signed(action_class="money_movement", grant_id="grant-money-001")],
        clock=lambda: NOW,
    )
    assert [code for _, code in runtime.refused] == ["unsigned", "signature_invalid", "always_human_refused"]
    assert runtime.decide("operational", "everything").auto_approve is False
    decision = runtime.decide("money_movement", "teracorp-ops")
    assert decision.auto_approve is False
    assert decision.reason == "always_human"


def test_runtime_re_checks_expiry_at_decision_time() -> None:
    clock = {"now": NOW}
    runtime = GRANT.AutonomyRuntime(POLICY, contract(), grants=[signed()], clock=lambda: clock["now"])
    assert runtime.decide("operational", "teracorp-ops").auto_approve is True
    clock["now"] = NOW + timedelta(days=31)
    decision = runtime.decide("operational", "teracorp-ops")
    assert decision.auto_approve is False
    assert decision.reason == "human_required"


def test_verify_binds_the_grant_to_a_candidate_evidence_packet() -> None:
    GRANT.verify_grant(signed(), POLICY, contract(), now=NOW, evidence=SAMPLE_EVIDENCE)
    weaker = copy.deepcopy(SAMPLE_EVIDENCE)
    weaker["incident_count_90d"] = 3
    with pytest.raises(GRANT.GrantRefused, match="^evidence_digest_mismatch$"):
        GRANT.verify_grant(signed(), POLICY, contract(), now=NOW, evidence=weaker)
    signed_weaker = signed(evidence_sha256=GRANT.evidence_digest(weaker))
    with pytest.raises(GRANT.GrantRefused, match="^evidence_not_candidate$"):
        GRANT.verify_grant(signed_weaker, POLICY, contract(), now=NOW, evidence=weaker)


def test_cli_sign_and_verify_round_trip(tmp_path: Path, capsys) -> None:
    key_file = tmp_path / "founder.key"
    key_file.write_text(base64.b64encode(FOUNDER_SEED).decode("ascii"), encoding="ascii")
    key_file.chmod(0o600)
    grant_file = tmp_path / "grant.json"
    grant_file.write_text(json.dumps(unsigned()), encoding="utf-8")
    trust_file = tmp_path / "autonomy.yaml"
    import yaml

    trust_file.write_text(yaml.safe_dump(contract()), encoding="utf-8")
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps(SAMPLE_EVIDENCE), encoding="utf-8")

    assert GRANT.main(["digest", str(evidence_file)]) == 0
    assert capsys.readouterr().out.strip() == unsigned()["evidence_sha256"]
    assert GRANT.main(["sign", str(grant_file), "--key-file", str(key_file), "--key-id", "synthetic-founder"]) == 0
    signed_text = capsys.readouterr().out
    assert FOUNDER_SEED.hex() not in signed_text
    signed_file = tmp_path / "signed.json"
    signed_file.write_text(signed_text, encoding="utf-8")
    args = ["verify", str(signed_file), "--contract", str(trust_file), "--now", "2026-09-28T12:00:00Z"]
    assert GRANT.main([*args, "--evidence", str(evidence_file)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    assert GRANT.main(["verify", str(grant_file), "--contract", str(trust_file), "--now", "2026-09-28T12:00:00Z"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": "unsigned"}


def test_cli_refuses_a_group_or_world_readable_key_file(tmp_path: Path, capsys) -> None:
    key_file = tmp_path / "founder.key"
    key_file.write_text(base64.b64encode(FOUNDER_SEED).decode("ascii"), encoding="ascii")
    key_file.chmod(0o644)
    grant_file = tmp_path / "grant.json"
    grant_file.write_text(json.dumps(unsigned()), encoding="utf-8")
    assert GRANT.main(["sign", str(grant_file), "--key-file", str(key_file), "--key-id", "synthetic-founder"]) == 2
    captured = capsys.readouterr()
    assert "permissions" in captured.err
    assert base64.b64encode(FOUNDER_SEED).decode("ascii") not in captured.err + captured.out


def test_grant_schema_accepts_signed_grants_and_rejects_unsigned_or_widened_ones() -> None:
    from jsonschema import Draft202012Validator

    schema_path = SCRIPTS.parents[1] / "contracts/approvals/autonomy-grant.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(signed())) == []
    assert list(validator.iter_errors(unsigned()))
    for mutate in (
        lambda g: g.update(approver_role="operator"),
        lambda g: g.update(extra=True),
        lambda g: g["scope"].update(profiles=[]),
        lambda g: g["signature"].update(algorithm="hmac-sha256"),
    ):
        grant = signed()
        mutate(grant)
        assert list(validator.iter_errors(grant))
