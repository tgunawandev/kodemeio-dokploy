"""Unit tests for gen_hermes in generate.py."""

from __future__ import annotations

import pytest
from generate import HERMES_DEFAULT_UPSTREAM_REF, gen_hermes


def test_gen_hermes_kod_telegram_only():
    """kod-shape tenant (Telegram only, no Honcho) → kod-infra-hermes.yaml + env example."""
    tenant = {
        "code": "kod",
        "name": "Kodemeio",
        "short_name": "KOD",
        "domain": "kodeme.io",
    }
    hermes = {
        "enabled": True,
        "server": "kod-prod-04-claude",
        "inbound": {
            "telegram": {"enabled": True},
            "mattermost": {"enabled": False},
        },
        "memory": {"honcho": {"enabled": False}},
    }

    y_name, y_content, e_name, e_content = gen_hermes(tenant, hermes, "production")

    # Filenames
    assert y_name == "kod-infra-hermes.yaml"
    assert e_name == ".env.kod-infra-hermes.example"

    # Manifest content
    assert "server: kod-prod-04-claude" in y_content
    assert "project: kod" in y_content
    assert "extends: ../../bases/hermes.yaml" in y_content
    assert "Telegram inbound" in y_content  # description

    # Env example content
    assert "TELEGRAM_BOT_TOKEN=CHANGE_ME" in e_content
    assert "MATTERMOST_TOKEN=CHANGE_ME" not in e_content  # mattermost inbound block omitted
    assert "HONCHO_API_KEY=" not in e_content  # honcho block omitted
    assert "GATEWAY_ALLOW_ALL_USERS" not in e_content  # only set when Mattermost-only
    assert "MATTERMOST_ALLOWED_USERS" not in e_content


def test_gen_hermes_tpp_mattermost_only():
    """tpp-shape (Mattermost only) → gateway allowlist block present, fail-closed."""
    tenant = {
        "code": "tpp",
        "name": "TPP",
        "short_name": "TPP",
        "domain": "idtpp.com",
    }
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {
            "telegram": {"enabled": False},
            "mattermost": {
                "enabled": True,
                "url": "https://mm.idtpp.com",
                "reply_mode": "thread",
                "require_mention": True,
                "allowed_channels": ["nxe8gt7xyfbp9rxqdqso5m17dr"],
            },
        },
        "memory": {"honcho": {"enabled": False}},
    }

    y_name, y_content, e_name, e_content = gen_hermes(tenant, hermes, "production")

    assert y_name == "tpp-infra-hermes.yaml"
    assert "server: tpp-prod-01" in y_content
    assert "Mattermost inbound (mm.idtpp.com)" in y_content
    assert "MCP outbound" in y_content

    # env.example: Mattermost block present
    assert "MATTERMOST_URL=https://mm.idtpp.com" in e_content
    assert "MATTERMOST_TOKEN=CHANGE_ME" in e_content
    assert "MATTERMOST_REPLY_MODE=thread" in e_content
    assert "MATTERMOST_REQUIRE_MENTION=true" in e_content
    assert "MATTERMOST_ALLOWED_CHANNELS=nxe8gt7xyfbp9rxqdqso5m17dr" in e_content

    # Telegram and Honcho blocks omitted
    assert "TELEGRAM_BOT_TOKEN" not in e_content
    assert "HONCHO_API_KEY" not in e_content

    # Gateway allowlist block emitted because Mattermost is the ONLY inbound.
    # Fail-closed: upstream enforces MATTERMOST_ALLOWED_USERS as of v2026.8.31.
    assert "GATEWAY_ALLOW_ALL_USERS=false" in e_content
    assert "GATEWAY_ALLOW_ALL_USERS=true" not in e_content
    assert "MATTERMOST_ALLOWED_USERS=CHANGE_ME" in e_content


def test_gen_hermes_mac_with_honcho():
    """mac shape + honcho.enabled: true → workspace defaults to mac-hermes."""
    tenant = {
        "code": "mac",
        "name": "Mandiri Agro",
        "short_name": "MAC",
        "domain": "idtpp.com",
    }
    hermes = {
        "enabled": True,
        "server": "tpp-prod-05",
        "inbound": {
            "telegram": {"enabled": False},
            "mattermost": {
                "enabled": True,
                "url": "https://mac-mm.idtpp.com",
                "allowed_channels": ["bnms3rw37iya5b8g1fdyg3edph"],
            },
        },
        "memory": {"honcho": {"enabled": True}},  # workspace not set → default
    }

    _, y_content, _, e_content = gen_hermes(tenant, hermes, "production")

    assert "Honcho memory (mac-hermes)" in y_content
    assert "HONCHO_API_KEY=CHANGE_ME" in e_content
    assert "HONCHO_BASE_URL=https://honcho.kodeme.io" in e_content
    assert "HONCHO_WORKSPACE=mac-hermes" in e_content


def test_gen_hermes_requires_server():
    tenant = {"code": "tpp", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        # server missing
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.idtpp.com"}},
        "memory": {"honcho": {"enabled": False}},
    }

    with pytest.raises(ValueError, match="hermes.server is required"):
        gen_hermes(tenant, hermes, "production")


def test_gen_hermes_requires_at_least_one_inbound():
    tenant = {"code": "tpp", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {
            "telegram": {"enabled": False},
            "mattermost": {"enabled": False},
        },
        "memory": {"honcho": {"enabled": False}},
    }

    with pytest.raises(ValueError, match="no inbound adapter is enabled"):
        gen_hermes(tenant, hermes, "production")


def test_gen_hermes_mattermost_requires_url():
    tenant = {"code": "tpp", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {
            "mattermost": {"enabled": True},  # url missing
        },
        "memory": {"honcho": {"enabled": False}},
    }

    with pytest.raises(ValueError, match="mattermost.url is required"):
        gen_hermes(tenant, hermes, "production")


def test_gen_hermes_backup_gap_comment_on_tpp_prod():
    """Servers matching ^tpp-prod-\\d+$ → backup-gap comment appended."""
    tenant = {"code": "tpp", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.idtpp.com"}},
        "memory": {"honcho": {"enabled": False}},
    }
    _, y_content, _, _ = gen_hermes(tenant, hermes, "production")

    assert "kodemeio-s3-backups" in y_content
    assert "dokploy.idtpp.com" in y_content


def test_gen_hermes_no_backup_gap_comment_on_kod_prod():
    tenant = {"code": "kod", "short_name": "KOD", "domain": "kodeme.io"}
    hermes = {
        "enabled": True,
        "server": "kod-prod-04-claude",
        "inbound": {"telegram": {"enabled": True}},
        "memory": {"honcho": {"enabled": False}},
    }
    _, y_content, _, _ = gen_hermes(tenant, hermes, "production")

    assert "kodemeio-s3-backups" not in y_content


def test_gen_hermes_idempotent():
    """Two consecutive calls produce identical output."""
    tenant = {"code": "tpp", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {"mattermost": {"enabled": True, "url": "https://mm.idtpp.com"}},
        "memory": {"honcho": {"enabled": False}},
    }

    out1 = gen_hermes(tenant, hermes, "production")
    out2 = gen_hermes(tenant, hermes, "production")
    assert out1 == out2


def _kod_superuser():
    tenant = {
        "code": "kod",
        "name": "Kodemeio",
        "short_name": "KOD",
        "domain": "kodeme.io",
    }
    hermes = {
        "enabled": True,
        "server": "kod-prod-04-claude",
        "inbound": {"telegram": {"enabled": True}, "mattermost": {"enabled": False}},
        "memory": {"honcho": {"enabled": False}},
        "edition": "superuser",
    }
    return tenant, hermes


def _tpp_business():
    tenant = {"code": "tpp", "name": "TPP", "short_name": "TPP", "domain": "idtpp.com"}
    hermes = {
        "enabled": True,
        "server": "tpp-prod-01",
        "inbound": {
            "telegram": {"enabled": False},
            "mattermost": {
                "enabled": True,
                "url": "https://mm.idtpp.com",
                "allowed_channels": ["c1"],
            },
        },
        "memory": {"honcho": {"enabled": False}},
        "edition": "business",
    }
    return tenant, hermes


def test_gen_hermes_superuser_emits_overrides_and_pat():
    tenant, hermes = _kod_superuser()
    _, y, _, e = gen_hermes(tenant, hermes, "production")
    assert "env_overrides:" in y
    assert "HERMES_EDITION: superuser" in y
    assert "HERMES_CPU_LIMIT: '4.0'" in y
    assert "HERMES_MEM_LIMIT: 6G" in y
    assert "HERMES_MEM_RESERVATION: 1G" in y
    assert "HERMES_EDITION=superuser" in e
    assert "HERMES_WORKSPACE_GH_PAT=CHANGE_ME" in e


def test_gen_hermes_business_no_pat():
    tenant, hermes = _tpp_business()
    _, y, _, e = gen_hermes(tenant, hermes, "production")
    assert "HERMES_EDITION: business" in y
    assert "HERMES_CPU_LIMIT: '2.0'" in y
    assert "HERMES_MEM_LIMIT: 2G" in y
    assert "HERMES_MEM_RESERVATION: 512M" in y
    assert "HERMES_EDITION=business" in e
    assert "HERMES_WORKSPACE_GH_PAT" not in e


def test_gen_hermes_edition_defaults_to_business():
    tenant, hermes = _tpp_business()
    del hermes["edition"]
    _, y, _, e = gen_hermes(tenant, hermes, "production")
    assert "HERMES_EDITION: business" in y
    assert "HERMES_MEM_LIMIT: 2G" in y
    assert "HERMES_WORKSPACE_GH_PAT" not in e


def test_gen_hermes_invalid_edition_raises():
    tenant, hermes = _tpp_business()
    hermes["edition"] = "admin"
    with pytest.raises(ValueError, match="edition"):
        gen_hermes(tenant, hermes, "production")


# --- upstream_ref: tenant-scoped image pin -----------------------------------


def test_gen_hermes_upstream_ref_defaults_to_fleet_default():
    """No hermes.upstream_ref → the fleet default, in BOTH manifest and example."""
    tenant, hermes = _tpp_business()
    assert "upstream_ref" not in hermes
    _, y, _, e = gen_hermes(tenant, hermes, "production")
    assert f"HERMES_UPSTREAM_REF: {HERMES_DEFAULT_UPSTREAM_REF}" in y
    assert f"HERMES_UPSTREAM_REF={HERMES_DEFAULT_UPSTREAM_REF}" in e


def test_gen_hermes_upstream_ref_pin_is_tenant_scoped():
    """A pinned ref lands in env_overrides (authoritative) and the example."""
    tenant, hermes = _tpp_business()
    hermes["upstream_ref"] = "v2026.8.31"
    _, y, _, e = gen_hermes(tenant, hermes, "production")
    assert "HERMES_UPSTREAM_REF: v2026.8.31" in y
    assert "HERMES_UPSTREAM_REF=v2026.8.31" in e

    # Pinning tpp must NOT move a tenant that did not opt in.
    kod_tenant, kod_hermes = _kod_superuser()
    _, kod_y, _, kod_e = gen_hermes(kod_tenant, kod_hermes, "production")
    assert f"HERMES_UPSTREAM_REF: {HERMES_DEFAULT_UPSTREAM_REF}" in kod_y
    assert f"HERMES_UPSTREAM_REF={HERMES_DEFAULT_UPSTREAM_REF}" in kod_e


@pytest.mark.parametrize("bad", ["", "   ", None, 20268, ["v1"]])
def test_gen_hermes_invalid_upstream_ref_raises(bad):
    tenant, hermes = _tpp_business()
    hermes["upstream_ref"] = bad
    with pytest.raises(ValueError, match="upstream_ref must be a non-empty string"):
        gen_hermes(tenant, hermes, "production")


KOD = {"code": "kod", "name": "Kodemeio", "short_name": "KOD", "domain": "kodeme.io"}


def _vision_hermes(**over):
    base = {
        "enabled": True,
        "server": "kod-prod-02-hermes",
        "edition": "business",
        "persona": "vision",
        "upstream_ref": "v2026.8.31",
        "inbound": {"telegram": {"enabled": True}, "mattermost": {"enabled": False}},
        "memory": {"honcho": {"enabled": False}},
    }
    base.update(over)
    return base


def test_gen_hermes_emits_persona_into_env_overrides():
    y_name, y, e_name, e = gen_hermes(KOD, _vision_hermes(), "production", agent_name="vision")
    assert y_name == "kod-infra-hermes-vision.yaml"
    assert e_name == ".env.kod-infra-hermes-vision.example"
    # env_overrides is applied AFTER env_file, so this is what makes the
    # persona survive a `deploy apply` that pushes the local .env wholesale.
    assert "HERMES_PERSONA: vision" in y
    assert "HERMES_PERSONA=vision" in e


def test_gen_hermes_omits_tenant_when_persona_is_not_tenanted():
    _, y, _, _ = gen_hermes(KOD, _vision_hermes(), "production", agent_name="vision")
    assert "HERMES_TENANT" not in y


def test_gen_hermes_emits_tenant_for_a_tenanted_persona():
    h = _vision_hermes(persona="karen", tenant="kodemeio")
    _, y, _, _ = gen_hermes(KOD, h, "production", agent_name="karen")
    assert "HERMES_PERSONA: karen" in y
    assert "HERMES_TENANT: kodemeio" in y


def test_gen_hermes_rejects_unknown_persona():
    with pytest.raises(ValueError, match="unknown persona"):
        gen_hermes(KOD, _vision_hermes(persona="jarvis2"), "production", agent_name="x")


def test_gen_hermes_rejects_persona_edition_mismatch():
    # vision is a business persona. Catching this here costs a test run;
    # catching it at container start costs a failed deploy.
    with pytest.raises(ValueError, match="requires edition"):
        gen_hermes(KOD, _vision_hermes(edition="superuser"), "production", agent_name="vision")


def test_gen_hermes_requires_tenant_for_a_tenanted_persona():
    with pytest.raises(ValueError, match="is tenanted"):
        gen_hermes(KOD, _vision_hermes(persona="karen"), "production", agent_name="karen")


def test_gen_hermes_rejects_tenant_on_an_untenanted_persona():
    with pytest.raises(ValueError, match="not tenanted"):
        gen_hermes(KOD, _vision_hermes(tenant="kodemeio"), "production", agent_name="vision")


def test_gen_hermes_env_example_uses_the_mcp_names_init_actually_reads():
    # hermes-init.sh's ensure_mcp_servers reads HERMES_MCP_<NAME>_URL /
    # _TOKEN. The old MCP_ODOO_URL / MCP_ODOO_AUTH_TOKEN shape documented a
    # contract init does not implement, so a filled-in env file produced an
    # agent with zero MCP servers and no error.
    _, _, _, e = gen_hermes(KOD, _vision_hermes(), "production", agent_name="vision")
    assert "\nMCP_ODOO_URL=" not in e
    assert "\nMCP_ODOO_AUTH_TOKEN=" not in e
    assert "HERMES_MCP_ODOO_URL=" in e
    assert "HERMES_MCP_ODOO_TOKEN=" in e


def test_gen_hermes_without_persona_still_works():
    """Every existing tenant omits `persona:`; none of them may break."""
    h = _vision_hermes()
    del h["persona"]
    _, y, _, e = gen_hermes(KOD, h, "production")
    assert "HERMES_PERSONA" not in y
    assert "HERMES_PERSONA" not in e
