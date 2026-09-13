"""Public hostnames follow deploys/domains/*.yaml (kctl-dokploy domains move).

Only the PUBLIC name moves: instance name, database, env filename,
COMPOSE_PROJECT_NAME and backup prefix keep the {code}-odoo-{short}
convention. Without a live move the output is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import generate
import pytest
import yaml
from generate import (
    gen_accurate_sync,
    gen_nextjs_careers,
    gen_odoo,
    gen_react_pwa,
    generate_tenant,
    load_domain_moves,
    moved_hosts,
)

TPP = {"code": "tpp", "name": "Pakerti", "short_name": "Pakerti", "domain": "idtpp.com"}
MAC = {"code": "mac", "name": "Mandiri Agro", "short_name": "MAC", "domain": "idtpp.com"}
ERP = {"recipe": "import", "short": "erp", "workers": 4, "apps": ["bia"]}


def _move(instance: str, to: str, old: str, stage: str, environment: str = "production", **extra: object) -> dict:
    return {"instance": instance, "environment": environment, "to": to, "from": [old], "stage": stage, **extra}


@pytest.fixture
def moves(monkeypatch: pytest.MonkeyPatch) -> dict:
    table: dict = {}
    monkeypatch.setattr(generate, "DOMAIN_MOVES", table)
    return table


def _add(table: dict, move: dict) -> None:
    table[(move["instance"], move["environment"])] = move


def _odoo(entry: dict = ERP, env_name: str = "production", suffix: str = "") -> tuple[dict, str]:
    _, content, _, example = gen_odoo(
        TPP, entry, env_name=env_name, server="tpp-prod-03", dns_suffix=suffix, db_suffix="_stg" if suffix else ""
    )
    return yaml.safe_load(content), example


def test_no_move_keeps_the_convention(moves: dict) -> None:
    m, example = _odoo()
    assert (m["domain"]["host"], m["dns"]["name"]) == ("tpp-odoo-erp.idtpp.com", "tpp-odoo-erp")
    assert "DOMAIN_LEGACY" not in m["env_overrides"] and "DOMAIN_LEGACY" not in example


def test_a_planned_move_changes_nothing(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "planned"))
    assert _odoo()[0]["domain"]["host"] == "tpp-odoo-erp.idtpp.com"


def test_a_dual_move_serves_the_new_name_and_keeps_the_old_one(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "dual"))
    m, example = _odoo()
    assert (m["domain"]["host"], m["dns"]["name"], m["env_overrides"]["DOMAIN"]) == (
        "erp.idtpp.com",
        "erp",
        "erp.idtpp.com",
    )
    assert m["env_overrides"]["DOMAIN_LEGACY"] == "tpp-odoo-erp.idtpp.com"
    assert "DOMAIN_LEGACY=tpp-odoo-erp.idtpp.com\n" in example


def test_cleaned_drops_domain_legacy(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "cleaned"))
    m, _ = _odoo()
    assert m["domain"]["host"] == "erp.idtpp.com" and "DOMAIN_LEGACY" not in m["env_overrides"]


def test_staging_moves_are_keyed_by_the_staging_instance(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp-stg", "tpp-erp-stg.idtpp.com", "tpp-odoo-erp-stg.idtpp.com", "redirect", "staging"))
    assert _odoo(env_name="staging", suffix="-stg")[0]["domain"]["host"] == "tpp-erp-stg.idtpp.com"
    assert _odoo()[0]["domain"]["host"] == "tpp-odoo-erp.idtpp.com"


def test_identity_names_never_move(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "dual"))
    m, _ = _odoo()
    assert m["instance"]["name"] == "tpp-odoo-erp"
    assert m["env_overrides"]["COMPOSE_PROJECT_NAME"] == "tpp-odoo-erp"
    assert m["database"]["name"] == "tpp_odoo_erp"
    assert m["env_file"] == "../../env/production/.env.tpp-odoo-erp"


def test_existing_host_key_still_works_for_production_only(moves: dict) -> None:
    helpdesk = {"recipe": "helpdesk", "short": "helpdesk", "host": "helpdesk.idtpp.com"}
    assert _odoo(helpdesk)[0]["domain"]["host"] == "helpdesk.idtpp.com"
    assert _odoo(helpdesk, "staging", "-stg")[0]["domain"]["host"] == "tpp-odoo-helpdesk-stg.idtpp.com"
    _add(moves, _move("tpp-odoo-helpdesk", "desk.idtpp.com", "helpdesk.idtpp.com", "dual"))
    assert _odoo(helpdesk)[0]["domain"]["host"] == "desk.idtpp.com"


def test_react_follows_the_odoo_move_and_its_own_move(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "dual"))
    _, bia, _, _ = gen_react_pwa(TPP, ERP, "bia", "production", "tpp-prod-03", "", "")
    assert yaml.safe_load(bia)["env_overrides"]["VITE_BIA_API_BASE_URL"] == "https://erp.idtpp.com/bia/api"
    mac_erp = {"recipe": "erp", "short": "erp", "apps": ["erp"]}
    _add(moves, _move("mac-odoo-erp", "mac-erp.idtpp.com", "mac-odoo-erp.idtpp.com", "dual"))
    _add(moves, _move("mac-react-erp", "mmac.idtpp.com", "mac-erp.idtpp.com", "cleaned", redirect=False))
    _, content, _, env = gen_react_pwa(MAC, mac_erp, "erp", "production", "tpp-prod-02", "", "")
    m = yaml.safe_load(content)
    assert (m["domain"]["host"], m["dns"]["name"]) == ("mmac.idtpp.com", "mmac")
    assert m["env_overrides"]["VITE_ERP_API_BASE_URL"] == "https://mac-erp.idtpp.com"
    assert "VITE_ERP_OIDC_REDIRECT_URI=https://mmac.idtpp.com/auth/callback\n" in env
    assert moved_hosts("mac-react-erp", "production") == ("mmac.idtpp.com", None)


def test_careers_and_accurate_sync_follow_their_odoo_moves(moves: dict) -> None:
    _add(moves, _move("tpp-odoo-hrms", "hrms.idtpp.com", "tpp-odoo-hrms.idtpp.com", "redirect"))
    _add(moves, _move("tpp-odoo-erp", "erp.idtpp.com", "tpp-odoo-erp.idtpp.com", "dual"))
    _, careers, _, careers_env = gen_nextjs_careers(TPP, "production", "tpp-prod-03", "")
    c = yaml.safe_load(careers)["env_overrides"]
    assert c["API_URL"] == "https://hrms.idtpp.com"
    assert c["NEXT_PUBLIC_RECRUITMENT_API_URL"] == "https://hrms.idtpp.com/recruitment/api"
    assert "NEXT_PUBLIC_API_URL=https://hrms.idtpp.com\n" in careers_env
    _, sync, _, _ = gen_accurate_sync(
        TPP, {"odoo_ref": "erp", "tenants": ["tpp"]}, ERP, "production", "tpp-prod-03", "", ""
    )
    assert yaml.safe_load(sync)["env_overrides"]["ODOO_URL"] == "https://erp.idtpp.com"


def test_load_domain_moves_reads_every_file(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        yaml.safe_dump({"moves": [_move("x-odoo-erp", "x.example.com", "old.example.com", "dual")]})
    )
    (tmp_path / "b.yaml").write_text(
        yaml.safe_dump({"moves": [_move("y-odoo-erp", "y.example.com", "o.example.com", "planned", "staging")]})
    )
    assert set(load_domain_moves(tmp_path)) == {("x-odoo-erp", "production"), ("y-odoo-erp", "staging")}
    assert load_domain_moves(tmp_path / "missing") == {}


def test_entry_environments_limit_generation(moves: dict, tmp_path: Path) -> None:
    tenant_file = tmp_path / "zz.yaml"
    tenant_file.write_text(
        yaml.safe_dump(
            {
                "tenant": {"code": "zz", "name": "Zed", "domain": "example.com"},
                "environments": {
                    "production": {"server": "p1", "dns_suffix": "", "db_suffix": ""},
                    "staging": {"server": "s1", "dns_suffix": "-stg", "db_suffix": "_stg"},
                },
                "odoo": [
                    {"recipe": "import", "short": "erp"},
                    {"recipe": "helpdesk", "short": "helpdesk", "environments": ["production"]},
                ],
            }
        )
    )
    paths = {str(p) for p, _ in generate_tenant(tenant_file)}
    assert any(p.endswith("instances/production/zz-odoo-helpdesk.yaml") for p in paths)
    assert not any(p.endswith("instances/staging/zz-odoo-helpdesk.yaml") for p in paths)
    assert any(p.endswith("instances/staging/zz-odoo-erp.yaml") for p in paths)
