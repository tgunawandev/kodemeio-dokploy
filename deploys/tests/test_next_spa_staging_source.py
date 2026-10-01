"""Only deployable migrated SPAs use the Next preview branch in staging."""

import pytest
import yaml
from generate import NEXT_SPA_STAGING_APPS, NEXT_SPA_STAGING_BRANCH, gen_react_pwa

TENANT = {"code": "kod", "name": "Kodemeio", "domain": "kodeme.io"}
ODOO = {"short": "full"}


@pytest.mark.parametrize("app", sorted(NEXT_SPA_STAGING_APPS))
def test_migrated_staging_spa_uses_next_preview(app: str) -> None:
    _, content, _, _ = gen_react_pwa(TENANT, ODOO, app, "staging")
    source = yaml.safe_load(content)["source_overrides"]
    assert source["repo"] == "kodemeio-next"
    assert source["branch"] == NEXT_SPA_STAGING_BRANCH
    assert source["compose_path"] == f"compose/docker-compose.{app}.yml"


@pytest.mark.parametrize("app", ["dms", "eam", "erp", "hrm", "saas", "shop", "tpm"])
def test_production_source_is_unchanged(app: str) -> None:
    _, content, _, _ = gen_react_pwa(TENANT, ODOO, app, "production")
    source = yaml.safe_load(content)["source_overrides"]
    assert "repo" not in source
    assert "branch" not in source


def test_retired_staging_app_keeps_legacy_source() -> None:
    _, content, _, _ = gen_react_pwa(TENANT, ODOO, "wms", "staging")
    source = yaml.safe_load(content)["source_overrides"]
    assert "repo" not in source
    assert "branch" not in source


def test_eam_staging_uses_asset_addon_path() -> None:
    _, content, _, env = gen_react_pwa(TENANT, ODOO, "eam", "staging")
    assert yaml.safe_load(content)["env_overrides"]["VITE_EAM_API_BASE_URL"].endswith("/asset/api")
    assert any(line.startswith("VITE_EAM_API_BASE_URL=") and line.endswith("/asset/api") for line in env.splitlines())
