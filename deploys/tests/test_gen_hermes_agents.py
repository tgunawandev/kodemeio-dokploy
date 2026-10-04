"""Multiple Hermes agents per tenant.

FRIDAY (superuser/engineering) and JARVIS (business/helpdesk) both serve tenant
`tpp`, but gen_hermes hardcoded `{code}-infra-hermes` everywhere, so a second
agent could not even be expressed — there was no manifest to deploy.
"""

from __future__ import annotations

import pytest
from generate import gen_hermes_agents

TENANT = {"code": "tpp", "name": "Pakerti", "short_name": "TPP", "domain": "idtpp.com"}
INBOUND = {
    "telegram": {"enabled": False},
    "mattermost": {"enabled": True, "url": "https://mm.idtpp.com"},
}


def test_a_tenant_without_an_agents_list_still_yields_one_agent():
    """Backwards compatibility: every existing tenant file must keep working."""
    hermes = {"enabled": True, "server": "tpp-prod-04", "inbound": INBOUND}
    out = gen_hermes_agents(TENANT, hermes, "production")
    assert len(out) == 1
    assert out[0][0] == "tpp-infra-hermes.yaml"


def test_two_agents_produce_two_distinct_manifests():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [
            {"edition": "superuser"},
            {"name": "jarvis", "edition": "business"},
        ],
    }
    out = gen_hermes_agents(TENANT, hermes, "production")
    assert [o[0] for o in out] == ["tpp-infra-hermes.yaml", "tpp-infra-hermes-jarvis.yaml"]


def test_each_agent_gets_its_own_container_prefix():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [{"edition": "superuser"}, {"name": "jarvis", "edition": "business"}],
    }
    out = gen_hermes_agents(TENANT, hermes, "production")
    assert "HERMES_CONTAINER_PREFIX: tpp-infra-hermes\n" in out[0][1]
    assert "HERMES_CONTAINER_PREFIX: tpp-infra-hermes-jarvis" in out[1][1]


def test_an_agent_inherits_shared_config_and_overrides_its_own():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "edition": "superuser",
        "agents": [{}, {"name": "jarvis", "edition": "business"}],
    }
    out = gen_hermes_agents(TENANT, hermes, "production")
    assert "HERMES_EDITION: superuser" in out[0][1]
    assert "HERMES_EDITION: business" in out[1][1]
    # server was declared once, at the parent level, and both inherit it
    assert "server: tpp-prod-04" in out[0][1]
    assert "server: tpp-prod-04" in out[1][1]


def test_env_example_filenames_are_distinct_per_agent():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [{}, {"name": "jarvis", "edition": "business"}],
    }
    out = gen_hermes_agents(TENANT, hermes, "production")
    names = [o[2] for o in out]
    assert names == [".env.tpp-infra-hermes.example", ".env.tpp-infra-hermes-jarvis.example"]
    assert len(set(names)) == 2, "agents must not share an env file"


def test_two_unnamed_agents_are_refused_rather_than_silently_colliding():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [{}, {}],
    }
    with pytest.raises(ValueError, match="unique"):
        gen_hermes_agents(TENANT, hermes, "production")


def test_agent_model_can_be_governed_by_litellm_virtual_key():
    tenant = {"code": "kodemeio", "name": "Kodemeio", "short_name": "KOD"}
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.kodeme.io"}},
        "agents": [
            {
                "name": "vision",
                "edition": "business",
                "persona": "vision",
                "model": {
                    "name": "deepseek/deepseek-v4-flash",
                    "provider": "openai",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            }
        ],
    }
    _, manifest, _, env_example = gen_hermes_agents(tenant, hermes, "production")[0]
    assert "HERMES_INFERENCE_MODEL: deepseek/deepseek-v4-flash" in manifest
    assert "HERMES_MODEL_PROVIDER: openai" in manifest
    assert "HERMES_MODEL_BASE_URL: https://llm.kodeme.io/v1" in manifest
    assert "OPENAI_API_KEY=CHANGE_ME" in env_example
    assert "OPENAI_API_KEY:" not in manifest


def test_vision_auxiliary_generates_exact_overrides_without_secret_value():
    tenant = {"code": "kod", "name": "Kodemeio", "short_name": "KOD"}
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.kodeme.io"}},
        "agents": [
            {
                "name": "vision",
                "persona": "vision",
                "edition": "business",
                "model": {
                    "name": "deepseek/deepseek-v4-flash",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "vision_auxiliary": {
                    "name": "google/gemini-2.5-flash-lite",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            }
        ],
    }
    _, manifest, _, env_example = gen_hermes_agents(tenant, hermes, "production")[0]
    for setting in (
        "HERMES_VISION_MODEL: google/gemini-2.5-flash-lite",
        "HERMES_VISION_PROVIDER: vision-litellm",
        "HERMES_VISION_BASE_URL: https://llm.kodeme.io/v1",
        "HERMES_VISION_API_KEY_ENV: OPENAI_API_KEY",
    ):
        assert setting in manifest
    assert "HERMES_VISION_MODEL=google/gemini-2.5-flash-lite" in env_example
    assert "OPENAI_API_KEY=" not in manifest
    assert "OPENAI_API_KEY=CHANGE_ME" in env_example


@pytest.mark.parametrize(
    "override",
    [
        {
            "name": "google/gemini-2.5-flash",
            "provider": "vision-litellm",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        {
            "name": "google/gemini-2.5-flash-lite",
            "provider": "google",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        {
            "name": "google/gemini-2.5-flash-lite",
            "provider": "vision-litellm",
            "base_url": "https://example.com/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        {
            "name": "google/gemini-2.5-flash-lite",
            "provider": "vision-litellm",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "GOOGLE_API_KEY",
        },
        {
            "name": "google/gemini-2.5-flash-lite",
            "provider": "vision-litellm",
            "base_url": "http://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
    ],
    ids=["model", "provider", "direct-or-unapproved-url", "key-env", "http-url"],
)
def test_invalid_vision_auxiliary_governance_config_refuses(override):
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.kodeme.io"}},
        "agents": [
            {
                "name": "vision",
                "persona": "vision",
                "edition": "business",
                "model": {
                    "name": "deepseek/deepseek-v4-flash",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "vision_auxiliary": override,
            }
        ],
    }
    with pytest.raises(ValueError, match="vision_auxiliary"):
        gen_hermes_agents({"code": "kod", "name": "Kodemeio"}, hermes, "production")


def test_vision_auxiliary_is_refused_for_non_vision_persona():
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": INBOUND,
        "agents": [
            {
                "name": "jarvis",
                "persona": "jarvis",
                "edition": "business",
                "vision_auxiliary": {
                    "name": "google/gemini-2.5-flash-lite",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            }
        ],
    }
    with pytest.raises(ValueError, match="only valid for tenant kod persona vision"):
        gen_hermes_agents({"code": "kod", "name": "Kodemeio"}, hermes, "production")


def test_vision_auxiliary_is_refused_for_non_kod_vision_tenant():
    hermes = {
        "enabled": True,
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [
            {
                "name": "vision",
                "persona": "vision",
                "edition": "business",
                "model": {
                    "name": "deepseek/deepseek-v4-flash",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "vision_auxiliary": {
                    "name": "google/gemini-2.5-flash-lite",
                    "provider": "vision-litellm",
                    "base_url": "https://llm.kodeme.io/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            }
        ],
    }
    with pytest.raises(ValueError, match="only valid for tenant kod persona vision"):
        gen_hermes_agents(TENANT, hermes, "production")


@pytest.mark.parametrize(
    "main_model",
    [
        None,
        {
            "name": "deepseek/deepseek-v4-flash-lite",
            "provider": "vision-litellm",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
    ],
    ids=["missing", "wrong-model-alias"],
)
def test_vision_auxiliary_requires_approved_main_model(main_model):
    agent = {
        "name": "vision",
        "persona": "vision",
        "edition": "business",
        "vision_auxiliary": {
            "name": "google/gemini-2.5-flash-lite",
            "provider": "vision-litellm",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
    }
    if main_model is not None:
        agent["model"] = main_model
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.kodeme.io"}},
        "agents": [agent],
    }
    with pytest.raises(ValueError, match="requires the approved hermes.model"):
        gen_hermes_agents({"code": "kod", "name": "Kodemeio"}, hermes, "production")


@pytest.mark.parametrize(
    "model",
    [
        {
            "name": "deepseek-flash",
            "provider": "vision-litellm",
            "base_url": "http://llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        {
            "name": "deepseek-flash",
            "provider": "openai",
            "base_url": "https://llm.kodeme.io/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        },
        {
            "name": "deepseek-flash",
            "provider": "openai",
            "base_url": "https://user:pass@llm.kodeme.io/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        {
            "name": "deepseek-flash",
            "provider": "openai",
            "base_url": "https://llm.kodeme.io:444/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
    ],
    ids=["https-required", "unapproved-key-env", "embedded-credentials-refused", "nonstandard-port-refused"],
)
def test_invalid_agent_llm_governance_config_refuses(model):
    tenant = {"code": "kodemeio", "name": "Kodemeio", "short_name": "KOD"}
    hermes = {
        "enabled": True,
        "server": "kod-prod-02",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.kodeme.io"}},
        "agents": [{"name": "vision", "edition": "business", "persona": "vision", "model": model}],
    }
    with pytest.raises(ValueError, match="hermes.model"):
        gen_hermes_agents(tenant, hermes, "production")


def test_sentinel_observer_keeps_its_isolated_source_branch():
    import yaml

    hermes = {
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [
            {
                "name": "sentinel",
                "edition": "business",
                "persona": "sentinel-observer",
                "source_branch": "sentinel-rollout-20261004",
                "upstream_ref": "v2026.9.24",
            }
        ],
    }
    result = gen_hermes_agents(TENANT, hermes)
    doc = yaml.safe_load(result[0][1])
    assert doc["source"] == {"branch": "sentinel-rollout-20261004"}
    assert doc["env_overrides"]["HERMES_PERSONA"] == "sentinel-observer"
    assert doc["env_overrides"]["HERMES_EDITION"] == "business"


def test_invalid_hermes_source_branch_is_refused():
    hermes = {
        "server": "tpp-prod-04",
        "inbound": INBOUND,
        "agents": [{"name": "sentinel", "source_branch": "../../main"}],
    }
    with pytest.raises(ValueError, match="safe git branch"):
        gen_hermes_agents(TENANT, hermes)
