"""VISION's LiteLLM binding must agree across Hermes, LiteLLM and Dokploy (review A4 M2).

Three repos carry the same VISION strings -- the named provider, gateway URL, key
env var and the two exact model routes:

- kodemeio-hermes `config/personas/vision/gateway-profile.json` -- the SOURCE OF
  TRUTH (hermes-init.sh writes config.yaml from it and refuses to start VISION
  unless config.yaml and env match it, via `scripts/vision_policy_validate.py`);
- kodemeio-llmlite `config/keys.yaml` -- the `vision` virtual key's model scope;
- this repo -- `tenants/kod.yaml` (whose values `generate.py` pins) and the
  generated `instances/production/kod-infra-hermes-vision.yaml`.

Before this test a change in any one of them left every suite green and broke
VISION at runtime (401 from the key scope, or a startup refusal). The expected
values are not restated here: they come from the Hermes profile through the
Hermes validator's own `expected_env()`, so there is exactly one copy to edit.

Sibling handling is `test_ci_gates.missing_sibling_verdict`'s rule, as in
test_chatwoot_env_parity.py: absent locally -> skip; absent AND CI=true AND listed
in `CI_GATES_REQUIRED_SIBLINGS` -> fail (a checkout defect, not nothing-to-check).
The Dokploy-internal agreement (tenant vs generated instance) always runs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest
import test_ci_gates
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent

TENANT = REPO_ROOT / "deploys" / "tenants" / "kod.yaml"
INSTANCE = REPO_ROOT / "deploys" / "instances" / "production" / "kod-infra-hermes-vision.yaml"
LITELLM_INSTANCE = REPO_ROOT / "deploys" / "instances" / "production" / "kod-infra-litellm.yaml"

HERMES = "kodemeio-hermes"
HERMES_PROFILE = WORKSPACE_ROOT / HERMES / "config" / "personas" / "vision" / "gateway-profile.json"
HERMES_VALIDATOR = WORKSPACE_ROOT / HERMES / "scripts" / "vision_policy_validate.py"
LITELLM = "kodemeio-llmlite"
LITELLM_KEYS = WORKSPACE_ROOT / LITELLM / "config" / "keys.yaml"
LITELLM_PROD = WORKSPACE_ROOT / LITELLM / "config" / "config.prod.yaml"


def _require(repo: str, path: Path) -> None:
    if path.is_file():
        return
    verdict = test_ci_gates.missing_sibling_verdict(repo, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI)
    if verdict == "fail":
        pytest.fail(f"{repo} is listed in CI_GATES_REQUIRED_SIBLINGS but {path} does not exist -- checkout defect")
    pytest.skip(f"{repo} not checked out at {WORKSPACE_ROOT / repo}")


def _vision_agent() -> dict:
    tenant = yaml.safe_load(TENANT.read_text())
    agents = [a for a in tenant["hermes"]["agents"] if a.get("persona") == "vision"]
    assert len(agents) == 1, "tenants/kod.yaml must declare exactly one VISION agent"
    return agents[0]


def _instance_env() -> dict:
    return yaml.safe_load(INSTANCE.read_text())["env_overrides"]


def _hermes_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hermes_vision_policy_validate", HERMES_VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def hermes():
    _require(HERMES, HERMES_PROFILE)
    _require(HERMES, HERMES_VALIDATOR)
    module = _hermes_validator()
    return module, module.load_profile(HERMES_PROFILE)


# --- Dokploy-internal (always runs) ------------------------------------------


def test_tenant_and_generated_instance_agree():
    agent = _vision_agent()
    env = _instance_env()
    model, aux = agent["model"], agent["vision_auxiliary"]
    assert env["HERMES_PERSONA"] == "vision"
    assert env["HERMES_INFERENCE_MODEL"] == model["name"]
    assert env["HERMES_MODEL_PROVIDER"] == model["provider"]
    assert env["HERMES_MODEL_BASE_URL"] == model["base_url"]
    assert env["HERMES_VISION_MODEL"] == aux["name"]
    assert env["HERMES_VISION_PROVIDER"] == aux["provider"]
    assert env["HERMES_VISION_BASE_URL"] == aux["base_url"]
    assert env["HERMES_VISION_API_KEY_ENV"] == aux["api_key_env"] == model["api_key_env"]


def test_vision_gateway_host_is_the_dokploy_litellm_domain():
    host = yaml.safe_load(LITELLM_INSTANCE.read_text())["domain"]["host"]
    for block in (_vision_agent()["model"], _vision_agent()["vision_auxiliary"]):
        parts = urlsplit(block["base_url"])
        assert (parts.scheme, parts.hostname) == ("https", host)


# --- Hermes profile (source of truth) vs Dokploy ------------------------------


def test_dokploy_tenant_matches_hermes_profile(hermes):
    module, profile = hermes
    agent = _vision_agent()
    gateway = profile["gateway"]
    assert agent["model"] == {
        "name": module.field(profile, "main_model"),
        "provider": gateway["provider_name"],
        "base_url": gateway["base_url"],
        "api_key_env": gateway["key_env"],
    }
    assert agent["vision_auxiliary"] == {
        "name": module.field(profile, "vision_model"),
        "provider": gateway["provider_name"],
        "base_url": gateway["base_url"],
        "api_key_env": gateway["key_env"],
    }


def test_generated_instance_env_matches_hermes_profile(hermes):
    module, profile = hermes
    env = _instance_env()
    expected = module.expected_env(profile)
    assert {name: env.get(name) for name in expected} == expected
    forbidden = set(profile["forbidden_present_env"]) | set(profile["forbidden_credential_env"])
    assert not forbidden & set(env), "a direct-provider credential is rendered into the VISION instance"


# --- Hermes profile vs LiteLLM key scope --------------------------------------


def test_litellm_vision_key_scope_is_exactly_the_hermes_routes(hermes):
    _, profile = hermes
    _require(LITELLM, LITELLM_KEYS)
    _require(LITELLM, LITELLM_PROD)
    keys = {k["alias"]: k for k in yaml.safe_load(LITELLM_KEYS.read_text())["keys"]}
    alias = profile["gateway"]["litellm_key_alias"]
    assert alias in keys, f"LiteLLM keys.yaml has no `{alias}` virtual key"
    key = keys[alias]
    hermes_routes = {profile["models"]["main"], profile["models"]["vision"]}
    # Subset: Hermes never asks for a route the key cannot use (401 at runtime).
    # Equality: the key grants nothing Hermes does not use (least privilege).
    assert set(key["models"]) == hermes_routes
    assert key["metadata"]["profile"] == alias
    model_names = {m["model_name"] for m in yaml.safe_load(LITELLM_PROD.read_text())["model_list"]}
    assert hermes_routes <= model_names, "a VISION route is not a LiteLLM model_name"


def test_parity_detects_drift(hermes):
    """Mutation check: a drifted value in any copy is caught by the comparisons above."""
    module, profile = hermes
    drifted = json.loads(json.dumps(profile))
    drifted["models"]["vision"] = "google/other-vision"
    env = _instance_env()
    expected = module.expected_env(drifted)
    assert {name: env.get(name) for name in expected} != expected
    keys = {k["alias"]: k for k in yaml.safe_load(LITELLM_KEYS.read_text())["keys"]} if LITELLM_KEYS.is_file() else None
    if keys is not None:
        assert set(keys["vision"]["models"]) != {drifted["models"]["main"], drifted["models"]["vision"]}
