"""Provider-neutral webhook authentication seam for the R7/PAY2 candidate.

Every secret below is an invented synthetic test value; it is not, and must never become, a real
provider signing secret.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mor_candidate_webhook", ROOT / "candidate.py")
assert SPEC is not None and SPEC.loader is not None
MOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOR)

SYNTHETIC_SECRET = b"synthetic-r7-test-secret-not-real-0123456789"
OTHER_SYNTHETIC_SECRET = b"synthetic-r7-other-secret-not-real-987654321"
NOW = 1_790_000_000
TOLERANCE = 300


def body(event_id: str = "e1", kind: str = "payment.captured", *, provider_ref: str | None = None, **extra) -> bytes:
    payload = {
        "contract_version": "merchant_event.v1",
        "event_id": event_id,
        "provider_event_ref": provider_ref or f"evt:{event_id}",
        "event_type": kind,
        "occurred_at": "2026-09-28T10:00:00Z",
        "order_ref": "order:synthetic-1",
        "payment_ref": "payment:synthetic-1",
        "amount_minor": 1000,
        "currency": "USD",
        **extra,
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def sign(raw: bytes, timestamp: int | str, secret: bytes = SYNTHETIC_SECRET) -> str:
    digest = hmac.new(secret, f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verifier(**kwargs) -> object:
    return MOR.WebhookVerifier(SYNTHETIC_SECRET, tolerance_seconds=TOLERANCE, **kwargs)


def rejected(code: str):
    return pytest.raises(MOR.WebhookRejected, match=rf"^{code}$")


def test_valid_signature_within_tolerance_is_the_path_to_verified() -> None:
    raw = body()
    verified = verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)
    assert isinstance(verified, MOR.VerifiedEvent)
    assert verified.event["verification"] == {"status": "verified", "adapter": "hmac-sha256.v1"}
    result = MOR.reconcile(
        [verified], [{"order_ref": "order:synthetic-1", "amount_minor": 1000, "currency": "USD"}], []
    )
    assert result["outcome"] == "clean"
    assert result["payments"][0]["verification_state"] == "verified"


def test_module_signature_helper_matches_the_documented_scheme() -> None:
    raw = body()
    assert MOR.webhook_signature(SYNTHETIC_SECRET, str(NOW), raw) == sign(raw, NOW)


def test_tampered_raw_body_is_rejected() -> None:
    raw = body()
    signature = sign(raw, NOW)
    tampered = raw.replace(b'"amount_minor":1000', b'"amount_minor":9000')
    with rejected("signature_mismatch"):
        verifier().verify(tampered, str(NOW), signature, now=NOW)


def test_reformatted_but_semantically_equal_body_is_rejected() -> None:
    raw = body()
    signature = sign(raw, NOW)
    reformatted = json.dumps(json.loads(raw), indent=2).encode()
    with rejected("signature_mismatch"):
        verifier().verify(reformatted, str(NOW), signature, now=NOW)


def test_wrong_secret_is_rejected() -> None:
    raw = body()
    with rejected("signature_mismatch"):
        verifier().verify(raw, str(NOW), sign(raw, NOW, OTHER_SYNTHETIC_SECRET), now=NOW)


def test_signature_is_bound_to_the_timestamp() -> None:
    raw = body()
    signature = sign(raw, NOW - 10)
    with rejected("signature_mismatch"):
        verifier().verify(raw, str(NOW), signature, now=NOW)


@pytest.mark.parametrize("offset", [-TOLERANCE - 1, TOLERANCE + 1, -86_400, 86_400])
def test_timestamp_outside_tolerance_is_rejected_even_with_valid_signature(offset: int) -> None:
    raw = body()
    timestamp = NOW + offset
    with rejected("timestamp_outside_tolerance"):
        verifier().verify(raw, str(timestamp), sign(raw, timestamp), now=NOW)


@pytest.mark.parametrize("offset", [-TOLERANCE, TOLERANCE])
def test_timestamp_at_tolerance_boundary_is_accepted(offset: int) -> None:
    raw = body()
    timestamp = NOW + offset
    assert verifier().verify(raw, str(timestamp), sign(raw, timestamp), now=NOW).event["event_id"] == "e1"


def test_replayed_delivery_is_rejected() -> None:
    guard = verifier()
    raw = body()
    guard.verify(raw, str(NOW), sign(raw, NOW), now=NOW)
    with rejected("replayed_event"):
        guard.verify(raw, str(NOW), sign(raw, NOW), now=NOW + 1)


def test_resigned_event_reusing_provider_event_ref_is_rejected() -> None:
    guard = verifier()
    first = body("e1", provider_ref="evt:shared")
    guard.verify(first, str(NOW), sign(first, NOW), now=NOW)
    second = body("e2", "payment.refunded", provider_ref="evt:shared")
    with rejected("replayed_event"):
        guard.verify(second, str(NOW + 5), sign(second, NOW + 5), now=NOW + 5)


def test_replay_after_cache_pruning_is_still_refused_by_timestamp() -> None:
    guard = verifier()
    raw = body()
    guard.verify(raw, str(NOW), sign(raw, NOW), now=NOW)
    later = NOW + TOLERANCE + 60
    other = body("e2")
    guard.verify(other, str(later), sign(other, later), now=later)  # prunes the first entry
    with rejected("timestamp_outside_tolerance"):
        guard.verify(raw, str(NOW), sign(raw, NOW), now=later)


def test_replay_cache_fails_closed_when_full() -> None:
    guard = verifier(max_replay_entries=1)
    first = body("e1")
    guard.verify(first, str(NOW), sign(first, NOW), now=NOW)
    second = body("e2")
    with rejected("replay_cache_full"):
        guard.verify(second, str(NOW), sign(second, NOW), now=NOW)


def test_failed_verification_does_not_consume_the_replay_slot() -> None:
    guard = verifier()
    raw = body()
    with rejected("signature_mismatch"):
        guard.verify(raw, str(NOW), sign(raw, NOW, OTHER_SYNTHETIC_SECRET), now=NOW)
    assert guard.verify(raw, str(NOW), sign(raw, NOW), now=NOW).event["event_id"] == "e1"


def test_signed_body_cannot_self_declare_verification() -> None:
    raw = body(verification={"status": "verified", "adapter": "hmac-sha256.v1"})
    with rejected("invalid_event"):
        verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)


def test_signed_but_invalid_event_is_rejected() -> None:
    raw = body(kind="payment.teleported")
    with rejected("invalid_event"):
        verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)


def test_rotation_header_with_one_matching_signature_is_accepted() -> None:
    raw = body()
    header = f"{sign(raw, NOW, OTHER_SYNTHETIC_SECRET)},{sign(raw, NOW)}"
    assert verifier().verify(raw, str(NOW), header, now=NOW).event["event_id"] == "e1"


@pytest.mark.parametrize(
    ("timestamp", "signature", "code"),
    [
        ("12e5", None, "malformed_timestamp"),
        ("-1790000000", None, "malformed_timestamp"),
        ("", None, "malformed_timestamp"),
        ("1" * 13, None, "malformed_timestamp"),
        (str(NOW), "sha256=deadbeef", "malformed_signature"),
        (str(NOW), "v1=" + "z" * 64, "malformed_signature"),
        (str(NOW), "", "malformed_signature"),
        (str(NOW), ",".join(["v1=" + "0" * 64] * 9), "malformed_signature"),
    ],
)
def test_malformed_headers_are_rejected_with_fixed_codes(timestamp: str, signature: str | None, code: str) -> None:
    raw = body()
    with rejected(code):
        verifier().verify(raw, timestamp, signature if signature is not None else sign(raw, NOW), now=NOW)


def test_rejections_never_echo_untrusted_content() -> None:
    raw = body(event_id="secret-marker-value")
    try:
        verifier().verify(raw + b"x", "secret-marker-ts", "v1=secret-marker-sig", now=NOW)
    except MOR.WebhookRejected as exc:
        assert "secret-marker" not in str(exc)
    else:  # pragma: no cover - the call must reject
        pytest.fail("verification should have been rejected")


def test_oversized_body_is_rejected_before_hmac() -> None:
    raw = b" " * (MOR._MAX_WEBHOOK_BODY_BYTES + 1)
    with rejected("body_too_large"):
        verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)


@pytest.mark.parametrize("secret", [b"", b"short-synthetic", "synthetic-str-secret-not-bytes-0123456789"])
def test_weak_or_non_bytes_secret_is_refused(secret) -> None:
    with pytest.raises(ValueError, match="secret"):
        MOR.WebhookVerifier(secret)


@pytest.mark.parametrize("tolerance", [0, -1, 3601, True])
def test_tolerance_must_be_bounded(tolerance) -> None:
    with pytest.raises(ValueError, match="tolerance"):
        MOR.WebhookVerifier(SYNTHETIC_SECRET, tolerance_seconds=tolerance)


def test_verified_event_cannot_be_constructed_outside_the_verifier() -> None:
    raw = json.loads(body())
    raw["verification"] = {"status": "verified", "adapter": "hmac-sha256.v1"}
    with pytest.raises(TypeError):
        MOR.VerifiedEvent(raw, object())


def test_json_input_can_never_claim_verified() -> None:
    raw = json.loads(body())
    raw["verification"] = {"status": "verified", "adapter": "hmac-sha256.v1"}
    with pytest.raises(MOR.InputError, match="verification"):
        MOR.reconcile([raw], [{"order_ref": "order:synthetic-1", "amount_minor": 1000, "currency": "USD"}], [])


def test_verified_event_payload_is_read_only() -> None:
    raw = body()
    verified = verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)
    with pytest.raises(TypeError):
        verified.event["amount_minor"] = 1  # type: ignore[index]


def test_mixed_verified_and_unverified_history_reports_unverified() -> None:
    guard = verifier()
    capture = body("e1")
    verified = guard.verify(capture, str(NOW), sign(capture, NOW), now=NOW)
    refund = json.loads(body("e2", "payment.refunded"))
    refund["occurred_at"] = "2026-09-28T11:00:00Z"
    refund["amount_minor"] = 100
    refund["verification"] = {"status": "unverified", "adapter": "none"}
    result = MOR.reconcile(
        [verified, refund], [{"order_ref": "order:synthetic-1", "amount_minor": 1000, "currency": "USD"}], []
    )
    assert result["payments"][0]["verification_state"] == "unverified"


def test_unverified_duplicate_of_verified_event_is_idempotent_and_keeps_verification() -> None:
    raw = body()
    verified = verifier().verify(raw, str(NOW), sign(raw, NOW), now=NOW)
    copy = {**json.loads(raw), "verification": {"status": "unverified", "adapter": "none"}}
    result = MOR.reconcile(
        [copy, verified], [{"order_ref": "order:synthetic-1", "amount_minor": 1000, "currency": "USD"}], []
    )
    assert result["outcome"] == "clean"
    assert result["duplicate_count"] == 1
    assert result["payments"][0]["verification_state"] == "verified"
