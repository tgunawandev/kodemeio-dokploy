"""The CW1 Chatwoot env pair must not drift (final whole-slice review I1).

`deploys/env/production/.env.kod-app-chatwoot.example` is what the operator
copies every name from into Dokploy's env store; `kodemeio-chatwoot/.env.example`
is the reviewed source of truth those names are supposed to come from, and its
header says the two must stay identical except for values. They drifted anyway:
the late security fix (`15e4b8d`) added `WHATSAPP_APP_SECRET` and
`SAFE_FETCH_ALLOW_PRIVATE_NETWORK` to the chatwoot example and to both composes,
but nothing mirrored them into the dokploy example -- and nothing in
`deploys/tests/` referenced `kod-app-chatwoot` at all, so no test could catch it.

Why the two keys matter, in the two different ways:

- `WHATSAPP_APP_SECRET` is `${VAR:?...}` in the prod compose, so a stack
  rendered from the stale example refuses to start (fail-closed -- no forgery
  risk, but a deploy that cannot happen).
- `SAFE_FETCH_ALLOW_PRIVATE_NETWORK` is the one whose absence is SILENT: the
  agent bot is created, never receives a message, and every conversation
  quietly fails over to staff. It is also the one key here that must never be
  a "change-me" placeholder: Chatwoot reads it with
  `ActiveModel::Type::Boolean` (`lib/safe_fetch.rb:37`), which casts every
  string that is not an explicit false to TRUE -- verified in the pinned
  v4.18.0 image: "change-me" -> true, "" -> true.

Sibling handling is `test_ci_gates.missing_sibling_verdict`'s rule, reused
directly (not reinvented), the same way `test_backup_inventory.py` uses it:
sibling absent locally -> skip; absent AND CI=true AND `kodemeio-chatwoot`
listed in `CI_GATES_REQUIRED_SIBLINGS` -> fail, a checkout defect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import test_ci_gates

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent

DOKPLOY_EXAMPLE = REPO_ROOT / "deploys" / "env" / "production" / ".env.kod-app-chatwoot.example"
SIBLING_REPO = "kodemeio-chatwoot"
CHATWOOT_EXAMPLE = WORKSPACE_ROOT / SIBLING_REPO / ".env.example"
CHATWOOT_PROD_COMPOSE = WORKSPACE_ROOT / SIBLING_REPO / "docker-compose.prod.yml"

# The two keys the security fix added in kodemeio-chatwoot and forgot here;
# named explicitly so "present in the dokploy example" is checked even when the
# sibling is not checked out.
REQUIRED_IN_DOKPLOY_EXAMPLE = ("WHATSAPP_APP_SECRET", "SAFE_FETCH_ALLOW_PRIVATE_NETWORK")

_REQUIRED_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+):\?")
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


# --- pure parsing/check functions (the mutation tests drive these directly) ---


def env_keys(text: str) -> list[str]:
    """The KEY of every `KEY=value` line, in file order. Comments and blank
    lines are skipped; the value may itself contain `=`."""
    keys: list[str] = []
    for line in text.splitlines():
        match = _ASSIGNMENT_RE.match(line.strip())
        if match:
            keys.append(match.group(1))
    return keys


def env_value(text: str, key: str) -> str | None:
    """The raw value of `key` (first assignment wins), or None when absent."""
    for line in text.splitlines():
        match = _ASSIGNMENT_RE.match(line.strip())
        if match and match.group(1) == key:
            return line.split("=", 1)[1].strip()
    return None


def duplicate_keys(text: str) -> list[str]:
    """Keys assigned more than once. A duplicated name is a silent
    last-one-wins: the chatwoot example carried two `DOMAIN` lines (final
    review M1), which is exactly how a placeholder survives a cleanup."""
    keys = env_keys(text)
    return sorted({key for key in keys if keys.count(key) > 1})


def compose_required_keys(compose_text: str) -> set[str]:
    """Every `${VAR:?...}` in a compose file. docker compose REFUSES to render
    when one of these is unset, so every one must be a name the operator's
    example tells them to fill in."""
    return set(_REQUIRED_VAR_RE.findall(compose_text))


def parity_violations(dokploy_text: str, chatwoot_text: str) -> list[str]:
    """Key-set differences between the two examples, both directions."""
    dokploy = set(env_keys(dokploy_text))
    chatwoot = set(env_keys(chatwoot_text))
    violations = []
    only_dokploy = sorted(dokploy - chatwoot)
    only_chatwoot = sorted(chatwoot - dokploy)
    if only_dokploy:
        violations.append(f"only in the dokploy example: {only_dokploy}")
    if only_chatwoot:
        violations.append(f"only in kodemeio-chatwoot/.env.example: {only_chatwoot}")
    return violations


def missing_required_violations(dokploy_text: str, compose_text: str) -> list[str]:
    """Every compose-required (`:?`) variable that the dokploy example does
    not name -- a deploy rendered from the example would refuse to start."""
    keys = set(env_keys(dokploy_text))
    return sorted(compose_required_keys(compose_text) - keys)


def order_violations(dokploy_text: str, chatwoot_text: str) -> list[str]:
    """The two examples' KEY SEQUENCES must match, not just their sets: the
    header's claim is "identical except for values", and a reordered block is
    how a hand-merged key ends up in the wrong section (or duplicated in one).
    Comments may differ -- only KEY= lines are compared."""
    dokploy = env_keys(dokploy_text)
    chatwoot = env_keys(chatwoot_text)
    if set(dokploy) != set(chatwoot):
        return []  # the set check already reports this; don't pile on
    if len(dokploy) != len(chatwoot):
        # Same key set, different number of assignments: one file assigns a
        # name twice (the duplicate check's own case, reported there too).
        return [f"different assignment counts: dokploy {len(dokploy)}, chatwoot {len(chatwoot)}"]
    return [
        f"position {index}: dokploy {found!r} vs chatwoot {expected!r}"
        for index, (found, expected) in enumerate(zip(dokploy, chatwoot, strict=True))
        if found != expected
    ]


def _require_sibling() -> None:
    """Skip/fail when the kodemeio-chatwoot sibling is not checked out, per
    test_ci_gates' own sibling-repo convention."""
    if CHATWOOT_EXAMPLE.is_file():
        return
    verdict = test_ci_gates.missing_sibling_verdict(
        SIBLING_REPO, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI
    )
    if verdict == "fail":
        pytest.fail(
            f"{SIBLING_REPO} is listed in CI_GATES_REQUIRED_SIBLINGS but "
            f"{CHATWOOT_EXAMPLE} does not exist -- checkout defect, not nothing-to-check"
        )
    pytest.skip(
        f"{SIBLING_REPO} not checked out locally at {WORKSPACE_ROOT / SIBLING_REPO} -- "
        "the example's own shape and required keys are still checked below"
    )


# --- tests: the examples (always runnable) ------------------------------------


def test_the_two_keys_this_check_exists_for_are_in_the_dokploy_example():
    """I1's own regression pin: the app secret and the SafeFetch switch are
    named in the file the operator copies into Dokploy's env store."""
    text = DOKPLOY_EXAMPLE.read_text()
    keys = set(env_keys(text))
    missing = [key for key in REQUIRED_IN_DOKPLOY_EXAMPLE if key not in keys]
    assert not missing, f"deploys/env/production/.env.kod-app-chatwoot.example is missing {missing}"


def test_safe_fetch_is_an_explicit_boolean_in_the_dokploy_example():
    """`SAFE_FETCH_ALLOW_PRIVATE_NETWORK` must be a real boolean, never a
    placeholder: Chatwoot casts it with ActiveModel::Type::Boolean, which
    turns every string that is not an explicit false into TRUE (verified in
    the pinned v4.18.0 image: "change-me" -> true, "" -> true). A placeholder
    here would silently open loopback/link-local/RFC1918 to SafeFetch, the
    exact exposure the example's own comment documents."""
    value = env_value(DOKPLOY_EXAMPLE.read_text(), "SAFE_FETCH_ALLOW_PRIVATE_NETWORK")
    assert value in {"true", "false"}, (
        f"SAFE_FETCH_ALLOW_PRIVATE_NETWORK={value!r} -- must be exactly 'true' or 'false' "
        "(anything else casts to true in Chatwoot)"
    )


def test_no_duplicate_keys_in_the_dokploy_example():
    duplicates = duplicate_keys(DOKPLOY_EXAMPLE.read_text())
    assert not duplicates, f"deploys/env/production/.env.kod-app-chatwoot.example assigns twice: {duplicates}"


# --- tests: drift against the sibling repo (skip/fail per sibling rule) -------


def test_key_sets_of_the_two_env_examples_are_identical():
    _require_sibling()
    violations = parity_violations(DOKPLOY_EXAMPLE.read_text(), CHATWOOT_EXAMPLE.read_text())
    assert not violations, f"{DOKPLOY_EXAMPLE.name} and {SIBLING_REPO}/.env.example have drifted: {violations}"


def test_key_order_of_the_two_env_examples_is_identical():
    """The same keys in the same order -- "identical except for values" is a
    claim about the whole shape of the file, not just its key set."""
    _require_sibling()
    violations = order_violations(DOKPLOY_EXAMPLE.read_text(), CHATWOOT_EXAMPLE.read_text())
    assert not violations, f"the two env examples list their keys in different orders: {violations}"


def test_no_duplicate_keys_in_the_chatwoot_example():
    _require_sibling()
    duplicates = duplicate_keys(CHATWOOT_EXAMPLE.read_text())
    assert not duplicates, f"{SIBLING_REPO}/.env.example assigns twice: {duplicates}"


def test_chatwoot_example_makes_safe_fetch_an_explicit_boolean():
    _require_sibling()
    value = env_value(CHATWOOT_EXAMPLE.read_text(), "SAFE_FETCH_ALLOW_PRIVATE_NETWORK")
    assert value in {"true", "false"}, (
        f"SAFE_FETCH_ALLOW_PRIVATE_NETWORK={value!r} in {SIBLING_REPO}/.env.example -- "
        "anything but an explicit boolean casts to true in Chatwoot"
    )


def test_every_compose_required_variable_is_named_in_the_dokploy_example():
    """The direction that catches I1 even if the two examples were edited
    together but the compose grew a new `${VAR:?}`: a required variable the
    operator's example never names is a deploy that cannot render."""
    _require_sibling()
    missing = missing_required_violations(DOKPLOY_EXAMPLE.read_text(), CHATWOOT_PROD_COMPOSE.read_text())
    assert not missing, f"{CHATWOOT_PROD_COMPOSE.name} requires {missing}, which the dokploy example never names"


# --- mutation checks: prove the checks have teeth -----------------------------


def test_mutation_dropping_the_app_secret_from_the_dokploy_example_fails_parity():
    """I1's actual shape: the key existed in the chatwoot example and in the
    compose, and was simply absent here. Deleting it from an in-memory copy
    must fail the parity check and the required-key check -- not pass because
    today's file happens to be complete."""
    dokploy = DOKPLOY_EXAMPLE.read_text()
    assert "WHATSAPP_APP_SECRET=" in dokploy
    mutated = "\n".join(line for line in dokploy.splitlines() if not line.strip().startswith("WHATSAPP_APP_SECRET="))
    assert "WHATSAPP_APP_SECRET" in missing_required_violations(mutated, CHATWOOT_PROD_COMPOSE.read_text())
    if CHATWOOT_EXAMPLE.is_file():
        assert parity_violations(mutated, CHATWOOT_EXAMPLE.read_text())


def test_mutation_dropping_safe_fetch_fails_the_required_keys_check():
    """The silent one: no compose `:?` protects it, so only this explicit pin
    (and the parity check) stands between a deleted line and a bot that never
    receives a message."""
    dokploy = DOKPLOY_EXAMPLE.read_text()
    mutated = "\n".join(
        line for line in dokploy.splitlines() if not line.strip().startswith("SAFE_FETCH_ALLOW_PRIVATE_NETWORK=")
    )
    keys = set(env_keys(mutated))
    assert [key for key in REQUIRED_IN_DOKPLOY_EXAMPLE if key not in keys] == ["SAFE_FETCH_ALLOW_PRIVATE_NETWORK"]


def test_mutation_a_placeholder_safe_fetch_value_is_rejected():
    """The cast trap, pinned: "change-me" (the file's own placeholder
    convention) must fail the boolean check rather than being accepted."""
    text = DOKPLOY_EXAMPLE.read_text().replace(
        "SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true", "SAFE_FETCH_ALLOW_PRIVATE_NETWORK=change-me"
    )
    value = env_value(text, "SAFE_FETCH_ALLOW_PRIVATE_NETWORK")
    assert value == "change-me"
    assert value not in {"true", "false"}


def test_mutation_a_duplicate_key_is_caught():
    text = DOKPLOY_EXAMPLE.read_text() + "\nDOMAIN=change-me\n"
    assert duplicate_keys(text) == ["DOMAIN"]


def test_mutation_a_reordered_key_is_caught():
    """Prove the order check has teeth: move one key to the end and it fails
    (a hand merge that drops a key back into the wrong block looks exactly
    like this)."""
    if not CHATWOOT_EXAMPLE.is_file():
        pytest.skip("needs the kodemeio-chatwoot sibling to reorder against")
    dokploy = DOKPLOY_EXAMPLE.read_text()
    lines = dokploy.splitlines()
    moved = next(line for line in lines if line.strip().startswith("SMTP_DOMAIN="))
    mutated = "\n".join([line for line in lines if line != moved] + [moved])
    assert order_violations(mutated, CHATWOOT_EXAMPLE.read_text()), "moving a key must fail the order check"
    # ...and the set check must stay silent about it (the two are complements,
    # not duplicates): same keys, different order.
    assert parity_violations(mutated, CHATWOOT_EXAMPLE.read_text()) == []
