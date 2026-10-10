"""ENT (app entitlement bridge): app_checkout.v1 + entitlement.v1 contracts and signing vector."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import NamedTuple

import pytest
from contracts_lib import CONTRACTS, load, pii_hits, registry, schema_property_names, validator_for

ENT = CONTRACTS / "entitlements"
EXAMPLES = CONTRACTS / "examples" / "entitlements"
CHECKOUT = ENT / "app_checkout.v1.schema.json"
ENTITLEMENT = ENT / "entitlement.v1.schema.json"
SCHEMAS = {"app_checkout.v1": CHECKOUT, "entitlement.v1": ENTITLEMENT}
APPS = ENT / "apps.v1.json"


def registered_apps() -> list[str]:
    """The paid-app registry is data: an `app` value is valid iff it is listed in apps.v1.json."""
    return json.loads(APPS.read_text())["apps"]


class ContractError(NamedTuple):
    validator: str
    absolute_path: tuple
    message: str


def contract_errors(schema_path, payload) -> list[ContractError]:
    """Schema errors plus the registry check the schema deliberately does not pin."""
    errors = [
        ContractError(error.validator, tuple(error.absolute_path), error.message)
        for error in validator_for(schema_path).iter_errors(payload)
    ]
    app = payload.get("app") if isinstance(payload, dict) else None
    if isinstance(app, str) and not any(e.absolute_path == ("app",) for e in errors) and app not in registered_apps():
        errors.append(ContractError("registry", ("app",), f"{app!r} is not a registered app (apps.v1.json)"))
    return errors


def _schema(example):
    return SCHEMAS[example.name.split(".valid")[0].split(".invalid")[0].split(".recorded")[0]]


# `<stem>.invalid-<reason>.json` -> (validator keyword, instance path, token in message).
EXPECTED_INVALID = {
    "pii-field": ("additionalProperties", (), None),
    "unknown-app": ("registry", ("app",), "'teramart'"),
    "extra-field": ("additionalProperties", (), "'note'"),
}


def _reason(path):
    return path.name.split(".invalid-", 1)[1].removesuffix(".json")


def test_the_two_schemas_exist_with_their_ids():
    for stem, path in SCHEMAS.items():
        assert load(path)["$id"] == f"https://kodeme.io/contracts/entitlements/{stem}.schema.json"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.valid.json")), ids=lambda p: p.name)
def test_valid_examples_validate(path):
    validator_for(_schema(path)).validate(json.loads(path.read_text()))
    assert contract_errors(_schema(path), json.loads(path.read_text())) == []


def test_every_schema_has_a_valid_example_and_all_three_invalid_reasons():
    for stem in SCHEMAS:
        assert (EXAMPLES / f"{stem}.valid.json").is_file()
        for reason in EXPECTED_INVALID:
            assert (EXAMPLES / f"{stem}.invalid-{reason}.json").is_file(), f"{stem} lacks invalid-{reason}"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.invalid-*.json")), ids=lambda p: p.name)
def test_invalid_examples_fail_for_their_named_reason(path):
    reason = _reason(path)
    assert reason in EXPECTED_INVALID, f"{path.name}: declare its reason"
    keyword, where, token = EXPECTED_INVALID[reason]
    errors = contract_errors(_schema(path), json.loads(path.read_text()))
    assert errors, f"{path.name} unexpectedly validated"
    for error in errors:
        assert error.validator == keyword and error.absolute_path == where, error.message
        if token:
            assert token in error.message


def test_the_pii_example_is_refused_for_the_pii_property():
    for stem in SCHEMAS:
        example = json.loads((EXAMPLES / f"{stem}.invalid-pii-field.json").read_text())
        extra = set(example) - set(load(SCHEMAS[stem])["properties"])
        assert pii_hits(extra), f"{stem}: the pii example must add a PII-named property"


@pytest.mark.parametrize("path", list(SCHEMAS.values()), ids=lambda p: p.name)
def test_no_pii_property_names(path):
    names = schema_property_names(load(path), registry())
    assert not pii_hits(names), pii_hits(names)


def test_app_registry_is_data_and_keeps_the_three_paid_apps():
    # The allowed apps live in apps.v1.json (data); the schemas pin only the slug shape.
    assert registered_apps() == ["terakidz", "terafin", "terakona-studio"]
    for schema in SCHEMAS.values():
        app = load(schema)["properties"]["app"]
        assert "enum" not in app and "const" not in app
        assert app["pattern"] == "^[a-z][a-z0-9]*(-[a-z0-9]+)*$"
    for code in registered_apps():
        assert len(code) <= 32 and not contract_errors(CHECKOUT, {**_valid_checkout(), "app": code})


def _valid_checkout():
    return json.loads((EXAMPLES / "app_checkout.v1.valid.json").read_text())


@pytest.mark.parametrize("bad", ["Terakidz", "tera kidz", "-terakidz", "terakidz-", "a" * 33, ""])
def test_app_must_be_a_slug(bad):
    errors = contract_errors(CHECKOUT, {**_valid_checkout(), "app": bad})
    assert errors and all(e.validator != "registry" for e in errors)


def test_a_slug_outside_the_registry_is_refused_by_the_registry_not_the_schema():
    payload = {**_valid_checkout(), "app": "newapp"}
    assert not list(validator_for(CHECKOUT).iter_errors(payload))
    assert [e.validator for e in contract_errors(CHECKOUT, payload)] == ["registry"]


def test_every_example_and_recorded_app_is_registered():
    bodies = [json.loads(p.read_text()) for p in EXAMPLES.glob("*.valid.json")]
    for name in ("entitlement.v1.recorded.json", "signing.v1.vector.json"):
        bodies.append(json.loads(json.loads((EXAMPLES / name).read_text())["body"]))
    assert bodies and all(body["app"] in registered_apps() for body in bodies)


@pytest.mark.parametrize("field", ["app", "app_account_ref", "plan_code", "issued_at"])
def test_shared_fields_are_identical_in_both_contracts(field):
    # Inlined (the shared compat walker resolves whole-schema $refs only), so pin the copies.
    assert load(CHECKOUT)["properties"][field] == load(ENTITLEMENT)["properties"][field]


def test_timestamps_share_one_definition():
    props = load(ENTITLEMENT)["properties"]
    assert props["valid_from"] == props["valid_until"] == props["issued_at"]


def test_status_enum_is_active_or_revoked():
    assert load(ENTITLEMENT)["properties"]["status"]["enum"] == ["active", "revoked"]


@pytest.mark.parametrize(
    "field,bad",
    [
        ("app_account_ref", "user@example.test"),
        ("app_account_ref", "7F3C2A10-5B1E-4C7D-9A2F-0E6B8D4C1A23"),
        ("plan_code", "Premium 30d"),
        ("plan_code", "premium--30d"),
        ("idempotency_key", "short"),
        ("issued_at", "2026-09-28 03:00:00"),
        ("issued_at", "2026-09-28T03:00:00+07:00"),
    ],
)
def test_checkout_rejects_malformed_fields(field, bad):
    example = json.loads((EXAMPLES / "app_checkout.v1.valid.json").read_text())
    example[field] = bad
    assert list(validator_for(CHECKOUT).iter_errors(example))


@pytest.mark.parametrize(
    "field,bad",
    [("event_id", "01J9A1B2C3D4E5F6G7H8J9K0MI"), ("event_id", "not-a-ulid"), ("status", "expired")],
)
def test_entitlement_rejects_malformed_fields(field, bad):
    example = json.loads((EXAMPLES / "entitlement.v1.valid.json").read_text())
    example[field] = bad
    assert list(validator_for(ENTITLEMENT).iter_errors(example))


@pytest.mark.parametrize(
    "field", ["event_id", "app", "app_account_ref", "plan_code", "status", "valid_from", "valid_until", "issued_at"]
)
def test_entitlement_requires_every_field(field):
    example = json.loads((EXAMPLES / "entitlement.v1.valid.json").read_text())
    del example[field]
    assert list(validator_for(ENTITLEMENT).iter_errors(example))


def sign(key: str, source: str, timestamp: str, event_id: str, body: bytes) -> str:
    """Reference signer (README): mirrors order_intake's signature_payload."""
    payload = f"{source}.{timestamp}.{event_id}.".encode() + body
    return "sha256=" + hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


def test_signing_vector_reproduces():
    vector = json.loads((EXAMPLES / "signing.v1.vector.json").read_text())
    got = sign(vector["key"], vector["source"], vector["timestamp"], vector["event_id"], vector["body"].encode())
    assert got == vector["signature"]
    body = json.loads(vector["body"])
    validator_for(ENTITLEMENT).validate(body)
    assert body["event_id"] == vector["event_id"]
    assert vector["source"] == "odoo"
    assert "TEST-ONLY" in vector["_comment"]


def test_signature_binds_source_timestamp_event_and_body():
    vector = json.loads((EXAMPLES / "signing.v1.vector.json").read_text())
    base = (vector["key"], vector["source"], vector["timestamp"], vector["event_id"], vector["body"].encode())
    for i, mutated in enumerate(["terakidz", "1790564702", "01J9A1B2C3D4E5F6G7H8J9K0MQ", b"{}"], start=1):
        args = list(base)
        args[i] = mutated
        assert sign(*args) != vector["signature"]


def test_readme_names_the_four_headers_and_window():
    readme = (ENT / "README.md").read_text()
    for header in ("X-Webhook-Source", "X-Webhook-Timestamp", "X-Webhook-Event-Id", "X-Webhook-Signature"):
        assert header in readme
    assert "300 s" in readme and "`odoo`" in readme


def test_recorded_dispatcher_request_validates_and_verifies():
    """ENT T4: the request app_entitlement's Odoo dispatcher test recorded (byte-pinned there).

    The same file is replayed by kodemeio-supabase's entitlement-webhook Deno test and its body
    is applied by the pgTAP suite, so one recording ties the three legs together.
    """
    recorded = json.loads((EXAMPLES / "entitlement.v1.recorded.json").read_text())
    assert "TEST-ONLY" in recorded["_comment"]
    headers, body = recorded["headers"], recorded["body"]
    assert set(headers) == {"X-Webhook-Source", "X-Webhook-Timestamp", "X-Webhook-Event-Id", "X-Webhook-Signature"}
    event = json.loads(body)
    validator_for(ENTITLEMENT).validate(event)
    assert headers["X-Webhook-Source"] == "odoo"
    assert headers["X-Webhook-Event-Id"] == event["event_id"]
    assert body.encode() == json.dumps(event, separators=(",", ":"), sort_keys=True).encode(), "canonical body"
    expected = sign(recorded["key"], "odoo", headers["X-Webhook-Timestamp"], event["event_id"], body.encode())
    assert headers["X-Webhook-Signature"] == expected


def test_contract_files_carry_no_program_name():
    # Founder rule 2026-09-28: runtime identifiers stay generic (no program/brand name in code).
    files = [*ENT.glob("*.json"), *EXAMPLES.glob("*.json")]
    assert files
    program_name = "".join(("tera", "corp"))  # built from pieces so this guard never matches itself
    for path in files:
        assert program_name not in path.read_text().lower(), path.name
