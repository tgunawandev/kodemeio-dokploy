#!/usr/bin/env python3
"""Founder-signed earned-autonomy grants (Teracorp A10 / AU1) and the runtime policy consumer.

A grant (``contracts/approvals/autonomy-grant.v1.schema.json``) promotes ONE low-risk action class
to auto-approval for an explicit set of profiles until an expiry. It is honoured only when:

1. its shape is exactly v1 and ``approver_role`` is ``founder``;
2. it carries an Ed25519 signature, by a key listed in ``autonomy.v1.yaml`` ``trust_roots``, over
   the domain-separated canonical JSON of every other field (so any edit invalidates it);
3. it targets the approval policy version in force;
4. its class is in the ``promotable`` list — never an ``always_human`` class, never a ``never auto``
   class, and never an already-autonomous class (a no-op);
5. ``issued_at <= now < expires_at`` and the validity is within ``max_grant_days``.

``AutonomyRuntime`` is the consumer a policy engine (e.g. Odoo ``mcp_base`` classify) mirrors: it
verifies grants once, re-checks expiry on every decision, and refuses ``always_human`` classes
before anything else. The checked-in contract has no trust root, so today every grant is refused.
Private keys never live in this repository; ``sign`` exists for the founder's offline use.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_EVIDENCE_PATH = Path(__file__).resolve().parent / "teracorp_autonomy_evidence.py"
_spec = importlib.util.spec_from_file_location("teracorp_autonomy_evidence", _EVIDENCE_PATH)
assert _spec is not None and _spec.loader is not None
EVIDENCE = sys.modules.get("teracorp_autonomy_evidence")
if EVIDENCE is None:
    EVIDENCE = importlib.util.module_from_spec(_spec)
    sys.modules["teracorp_autonomy_evidence"] = EVIDENCE
    _spec.loader.exec_module(EVIDENCE)

DOMAIN = b"kodemeio.autonomy-grant.v1\n"
# Runtime trust anchor: comma-separated "sha256:<hex>" fingerprints of the founder's raw Ed25519
# public keys, set in founder-controlled deploy config (never in this repository). A contract trust
# root is honoured only when its fingerprint is anchored here.
ANCHOR_ENV = "KODEMEIO_AUTONOMY_TRUST_ANCHORS"
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_INSTANT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_GRANT_KEYS = {
    "schema_version",
    "grant_id",
    "action_class",
    "policy_version",
    "approver_role",
    "evidence_sha256",
    "scope",
    "issued_at",
    "expires_at",
}
_MAX_INPUT_BYTES = 65_536


class GrantRefused(ValueError):
    """A grant is not honoured; the message is a fixed code and never echoes grant content."""


@dataclass(frozen=True)
class VerifiedGrant:
    grant_id: str
    action_class: str
    profiles: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    key_id: str


@dataclass(frozen=True)
class Decision:
    auto_approve: bool
    reason: str
    grant_id: str | None = None


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def evidence_digest(evidence: Any) -> str:
    return hashlib.sha256(canonical(evidence)).hexdigest()


def signing_message(grant: dict[str, Any]) -> bytes:
    return DOMAIN + canonical({key: value for key, value in grant.items() if key != "signature"})


def key_fingerprint(raw_public_key: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw_public_key).hexdigest()


def anchors_from_env() -> frozenset[str]:
    raw = os.environ.get(ANCHOR_ENV, "")[:4096]
    return frozenset(item.strip() for item in raw.split(",") if _FINGERPRINT.fullmatch(item.strip()))


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise GrantRefused("grant_invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise GrantRefused("grant_invalid") from exc


def _check_shape(grant: Any) -> dict[str, Any]:
    if type(grant) is not dict or set(grant) - {"signature"} != _GRANT_KEYS:
        raise GrantRefused("grant_invalid")
    if type(grant["schema_version"]) is not int or grant["schema_version"] != 1:
        raise GrantRefused("grant_invalid")
    for key in ("grant_id", "action_class"):
        if not isinstance(grant[key], str) or not _ID.fullmatch(grant[key]):
            raise GrantRefused("grant_invalid")
    if type(grant["policy_version"]) is not int or grant["approver_role"] != "founder":
        raise GrantRefused("grant_invalid")
    if not isinstance(grant["evidence_sha256"], str) or not _SHA256.fullmatch(grant["evidence_sha256"]):
        raise GrantRefused("grant_invalid")
    scope = grant["scope"]
    if type(scope) is not dict or set(scope) != {"profiles"}:
        raise GrantRefused("grant_invalid")
    profiles = scope["profiles"]
    if (
        type(profiles) is not list
        or not 1 <= len(profiles) <= 20
        or len(set(map(str, profiles))) != len(profiles)
        or not all(isinstance(item, str) and _ID.fullmatch(item) for item in profiles)
    ):
        raise GrantRefused("grant_invalid")
    _instant(grant["issued_at"])
    _instant(grant["expires_at"])
    return grant


def _check_signature(grant: dict[str, Any], contract: dict[str, Any], anchors: frozenset[str]) -> str:
    signature = grant.get("signature")
    if signature is None:
        raise GrantRefused("unsigned")
    if type(signature) is not dict or set(signature) != {"key_id", "algorithm", "value"}:
        raise GrantRefused("signature_invalid")
    if signature["algorithm"] != "ed25519" or not isinstance(signature["value"], str):
        raise GrantRefused("signature_invalid")
    roots = {root["key_id"]: root for root in contract["trust_roots"]}
    root = roots.get(signature["key_id"]) if isinstance(signature["key_id"], str) else None
    if root is None:
        raise GrantRefused("unknown_key")
    try:
        raw_public_key = base64.b64decode(root["public_key"], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(raw_public_key)
    except (binascii.Error, ValueError) as exc:
        raise GrantRefused("signature_invalid") from exc
    if key_fingerprint(raw_public_key) not in anchors:
        raise GrantRefused("trust_root_not_anchored")
    try:
        raw = base64.b64decode(signature["value"][:128], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GrantRefused("signature_invalid") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != signature["value"]:
        raise GrantRefused("signature_invalid")  # exactly one accepted spelling per signature
    try:
        public_key.verify(raw, signing_message(grant))
    except InvalidSignature as exc:
        raise GrantRefused("signature_invalid") from exc
    return signature["key_id"]


def verify_grant(
    grant: Any,
    policy: dict[str, Any],
    contract: dict[str, Any] | None = None,
    *,
    now: datetime,
    evidence: Any = None,
    anchors: frozenset[str] | None = None,
) -> VerifiedGrant:
    """Return the verified grant or raise ``GrantRefused`` with a fixed reason code.

    ``evidence_sha256`` is founder-attested: ``sign`` refuses unless the packet binds, and ``verify``
    re-checks it when the packet is supplied; the runtime consumer does not hold the packet.
    """
    policy = EVIDENCE._validate_policy(policy)
    contract = (
        EVIDENCE._validate_autonomy_contract(contract, policy)
        if contract is not None
        else EVIDENCE._load_autonomy_contract(policy=policy)
    )
    grant = _check_shape(grant)
    key_id = _check_signature(grant, contract, anchors_from_env() if anchors is None else anchors)
    if grant["grant_id"] in contract["revoked_grant_ids"]:
        raise GrantRefused("revoked")
    if grant["policy_version"] != policy["version"]:
        raise GrantRefused("policy_version_mismatch")
    action_class = grant["action_class"]
    if action_class in policy["always_human"]:
        raise GrantRefused("always_human_refused")
    if action_class in contract["already_autonomous"]:
        raise GrantRefused("already_autonomous_noop")
    if action_class not in contract["promotable"] or action_class in contract["never_promotable"]:
        raise GrantRefused("not_promotable")
    issued, expires = _instant(grant["issued_at"]), _instant(grant["expires_at"])
    if expires <= issued or (expires - issued).total_seconds() > contract["max_grant_days"] * 86_400:
        raise GrantRefused("validity_too_long")
    if now.tzinfo is None:
        raise GrantRefused("clock_invalid")
    if now < issued:
        raise GrantRefused("not_yet_valid")
    if now >= expires:
        raise GrantRefused("expired")
    if evidence is not None:
        check_evidence_binding(grant, evidence, policy, contract)
    return VerifiedGrant(
        grant_id=grant["grant_id"],
        action_class=action_class,
        profiles=tuple(grant["scope"]["profiles"]),
        issued_at=issued,
        expires_at=expires,
        key_id=key_id,
    )


def check_evidence_binding(
    grant: dict[str, Any], evidence: Any, policy: dict[str, Any], contract: dict[str, Any]
) -> None:
    """The grant's digest must be this packet's, and the packet must be a candidate for its class."""
    if evidence_digest(evidence) != grant["evidence_sha256"]:
        raise GrantRefused("evidence_digest_mismatch")
    try:
        report = EVIDENCE.evaluate(copy.deepcopy(evidence), policy, contract)
    except EVIDENCE.InputError as exc:
        raise GrantRefused("evidence_not_candidate") from exc
    if report["status"] != "candidate_for_manual_review_unverified" or report["action_class"] != grant["action_class"]:
        raise GrantRefused("evidence_not_candidate")


def sign_grant(grant: dict[str, Any], private_seed: bytes, key_id: str) -> dict[str, Any]:
    """Founder-side helper: sign every field except ``signature``. Never used by the runtime."""
    if not isinstance(key_id, str) or not _ID.fullmatch(key_id) or len(private_seed) != 32:
        raise GrantRefused("signing_input_invalid")
    body = {key: value for key, value in grant.items() if key != "signature"}
    value = Ed25519PrivateKey.from_private_bytes(private_seed).sign(signing_message(body))
    return {**body, "signature": {"key_id": key_id, "algorithm": "ed25519", "value": base64.b64encode(value).decode()}}


class AutonomyRuntime:
    """Decide auto-approval for an action class; honours only verified founder grants."""

    def __init__(
        self,
        policy: dict[str, Any],
        contract: dict[str, Any] | None = None,
        *,
        grants: Iterable[Any],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        anchors: frozenset[str] | None = None,
    ) -> None:
        self._policy = EVIDENCE._validate_policy(policy)
        self._contract = (
            EVIDENCE._validate_autonomy_contract(contract, self._policy)
            if contract is not None
            else EVIDENCE._load_autonomy_contract(policy=self._policy)
        )
        self._clock = clock
        self.verified: list[VerifiedGrant] = []
        self.refused: list[tuple[str, str]] = []
        anchors = anchors_from_env() if anchors is None else anchors
        now = clock()
        grants = list(grants)
        labels = [
            grant["grant_id"]
            if type(grant) is dict and isinstance(grant.get("grant_id"), str) and _ID.fullmatch(grant["grant_id"])
            else f"#{index}"
            for index, grant in enumerate(grants)
        ]
        for grant, label in zip(grants, labels, strict=True):
            if labels.count(label) > 1:
                self.refused.append((label, "duplicate_grant_id"))  # ambiguous: honour none of them
                continue
            try:
                self.verified.append(verify_grant(grant, self._policy, self._contract, now=now, anchors=anchors))
            except GrantRefused as exc:
                self.refused.append((label, str(exc)))

    def decide(self, action_class: str, profile: str, *, auto_approve_draft: bool = False) -> Decision:
        if action_class in self._policy["always_human"]:
            return Decision(False, "always_human")
        if action_class in self._contract["never_promotable"]:
            return Decision(False, "never_auto")
        if action_class in self._contract["already_autonomous"]:
            return Decision(auto_approve_draft is True, "profile_auto_approve_draft")
        if action_class in self._contract["promotable"]:
            now = self._clock()
            for grant in self.verified:
                if (
                    grant.action_class == action_class
                    and profile in grant.profiles
                    and grant.issued_at <= now < grant.expires_at
                ):
                    return Decision(True, "verified_grant", grant.grant_id)
        return Decision(False, "human_required")


def _read_json(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise GrantRefused("input_too_large")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=EVIDENCE._reject_duplicate_keys,
            parse_int=EVIDENCE._bounded_json_int,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EVIDENCE.InputError, RecursionError) as exc:
        raise GrantRefused("input_invalid_json") from exc


def _read_private_seed(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise GrantRefused("key file permissions must be 0600 or stricter")
        raw = os.read(fd, 1024)
    finally:
        os.close(fd)
    try:
        seed = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GrantRefused("key file must hold a base64 32-byte Ed25519 seed") from exc
    if len(seed) != 32:
        raise GrantRefused("key file must hold a base64 32-byte Ed25519 seed")
    return seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    digest = sub.add_parser("digest", help="print the evidence_sha256 of an evidence packet")
    digest.add_argument("evidence", type=Path)
    sign = sub.add_parser("sign", help="founder-only: sign a grant with a local 0600 key file")
    sign.add_argument("grant", type=Path)
    sign.add_argument("--key-file", type=Path, required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--evidence", type=Path, required=True, help="candidate evidence packet the grant binds to")
    verify = sub.add_parser("verify", help="verify a grant against the trust roots")
    verify.add_argument("grant", type=Path)
    verify.add_argument("--contract", type=Path, default=EVIDENCE._AUTONOMY)
    verify.add_argument("--evidence", type=Path)
    verify.add_argument("--now", help="UTC instant YYYY-MM-DDTHH:MM:SSZ (default: system clock)")
    verify.add_argument("--anchor", action="append", help=f"sha256:<hex> trust anchor (default: ${ANCHOR_ENV})")
    args = parser.parse_args(argv)
    try:
        if args.command == "digest":
            print(evidence_digest(_read_json(args.evidence)))
            return 0
        policy = EVIDENCE._load_policy()
        if args.command == "sign":
            grant = _check_shape(_read_json(args.grant))
            check_evidence_binding(
                grant, _read_json(args.evidence), policy, EVIDENCE._load_autonomy_contract(policy=policy)
            )
            print(json.dumps(sign_grant(grant, _read_private_seed(args.key_file), args.key_id)))
            return 0
        contract = EVIDENCE._load_autonomy_contract(args.contract, policy)
        anchors = frozenset(item for item in args.anchor or [] if _FINGERPRINT.fullmatch(item)) if args.anchor else None
        now = _instant(args.now) if args.now else datetime.now(UTC).replace(microsecond=0)
        evidence = _read_json(args.evidence) if args.evidence else None
        grant = verify_grant(_read_json(args.grant), policy, contract, now=now, evidence=evidence, anchors=anchors)
    except GrantRefused as exc:
        if args.command == "verify":
            print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, EVIDENCE.InputError) as exc:
        message = str(exc) if isinstance(exc, EVIDENCE.InputError) else "input cannot be read"
        print(f"error: {message}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "verified",
                "grant_id": grant.grant_id,
                "action_class": grant.action_class,
                "profiles": list(grant.profiles),
                "expires_at": grant.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "key_id": grant.key_id,
                "evidence_bound": evidence is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
