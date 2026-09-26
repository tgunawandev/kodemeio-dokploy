"""Redaction contract v1 (Wave 0 spec D18, W14) + drift check against the Odoo
Sentry scrubber (kodemeio-odoo docker/sentry_init.py) when that sibling exists."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import test_ci_gates
import yaml

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "contracts" / "observability" / "redaction.v1.yaml"
SIBLING = "kodemeio-odoo"
SENTRY_INIT = test_ci_gates.WORKSPACE_ROOT / SIBLING / "docker" / "sentry_init.py"

# Every value pattern must have at least one positive and one negative example.
EXAMPLES = {
    "email": (["a@b.co", "ops.team+x@kodeme.io"], ["no at sign here", "user@localhost"]),
    "phone_id": (["081234567890", "+6281234567890", "62812345678"], ["12345", "0712345678"]),
    "nik": (["3201234567890123", "nik 3201234567890123 ok"], ["320123456789012", "32012345678901234"]),
    "npwp": (["01.234.567.8-901.000", "012345678901000"], ["01.234.567", "12.34"]),
    "bearer": (["Bearer abc.def-123", "authorization: bearer XYZ_9"], ["bearer", "bearer-token=x"]),
}
KEY_EXAMPLES = (
    [
        "password",
        "db_pass",
        "client_secret",
        "access_token",
        "api_key",
        "X-Api-Key",
        "session_id",
        "csrftoken",
        "authorization",
    ],
    ["login", "note", "user", "email_from", "name"],
)


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text())


def test_contract_shape(contract):
    assert contract["version"] == 1
    assert set(contract) == {
        "version",
        "deny_headers",
        "deny_key_patterns",
        "redact_value_patterns",
        "replacement",
        "synthetic_markers",
        "applies_to",
    }
    assert all(h == h.lower() for h in contract["deny_headers"]), "headers are compared lower-case"
    assert {"authorization", "cookie", "x-api-key"} <= set(contract["deny_headers"])
    assert contract["replacement"] == "[REDACTED]"
    assert contract["synthetic_markers"], "synthetic_markers must be non-empty"
    assert {"odoo-sentry", "application-logs"} <= set(contract["applies_to"])


def test_every_regex_compiles(contract):
    for p in contract["deny_key_patterns"]:
        re.compile(p)
    for p in contract["redact_value_patterns"].values():
        re.compile(p)


def test_every_value_pattern_has_examples(contract):
    assert set(contract["redact_value_patterns"]) == set(EXAMPLES)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_value_pattern_examples(contract, name):
    rx = re.compile(contract["redact_value_patterns"][name])
    pos, neg = EXAMPLES[name]
    for s in pos:
        assert rx.search(s), f"{name} should match {s!r}"
    for s in neg:
        assert not rx.search(s), f"{name} must not match {s!r}"


def test_key_patterns(contract):
    rxs = [re.compile(p) for p in contract["deny_key_patterns"]]
    pos, neg = KEY_EXAMPLES
    for k in pos:
        assert any(r.search(k) for r in rxs), f"key {k!r} should be denied"
    for k in neg:
        assert not any(r.search(k) for r in rxs), f"key {k!r} must not be denied"


# --- drift against kodemeio-odoo/docker/sentry_init.py ------------------------


def _sentry_constants() -> dict:
    if not SENTRY_INIT.is_file():
        verdict = test_ci_gates.missing_sibling_verdict(
            SIBLING, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI
        )
        if verdict == "fail":
            pytest.fail(f"{SIBLING} is required in CI but {SENTRY_INIT} does not exist")
        pytest.skip(f"{SIBLING} not checked out at {SENTRY_INIT.parent.parent} -- sync check skipped")
    tree = ast.parse(SENTRY_INIT.read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {
                "DENY_HEADERS",
                "DENY_KEY_PATTERNS",
                "REDACT_VALUE_PATTERNS",
                "REPLACEMENT",
                "SYNTHETIC_MARKERS",
            }:
                out[name] = ast.literal_eval(node.value)
    return out


def test_sentry_init_mirrors_contract(contract):
    c = _sentry_constants()
    assert set(c) == {
        "DENY_HEADERS",
        "DENY_KEY_PATTERNS",
        "REDACT_VALUE_PATTERNS",
        "REPLACEMENT",
        "SYNTHETIC_MARKERS",
    }, f"sentry_init.py constants missing: {set(c)}"
    assert set(c["DENY_HEADERS"]) == set(contract["deny_headers"])
    assert list(c["DENY_KEY_PATTERNS"]) == contract["deny_key_patterns"]
    assert dict(c["REDACT_VALUE_PATTERNS"]) == contract["redact_value_patterns"]
    assert c["REPLACEMENT"] == contract["replacement"]
    assert list(c["SYNTHETIC_MARKERS"]) == contract["synthetic_markers"]
