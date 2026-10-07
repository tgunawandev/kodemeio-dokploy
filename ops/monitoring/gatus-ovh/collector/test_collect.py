import importlib.util
import json
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("collector", HERE / "collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.mark.parametrize(
    "status,health,expected",
    [
        ("running", "healthy", True),
        ("running", "none", True),
        ("running", "unhealthy", False),
        ("restarting", "none", False),
        ("exited", "healthy", False),
    ],
)
def test_state_and_health(status, health, expected):
    check = {"container": "worker"}
    row = {"name": "/worker", "status": status, "health": health}
    assert collector.evaluate(check, [row])[0] is expected


def test_missing_ambiguous_and_swarm_replacement():
    check = {"swarm_service": "dokploy"}
    row = {"name": "/dokploy.1.new", "swarm_service": "dokploy", "status": "running", "health": "healthy"}
    assert collector.evaluate(check, [])[0] is False
    assert collector.evaluate(check, [row, row])[0] is False
    assert collector.evaluate(check, [row])[0] is True


def test_catalog_covers_unique_native_endpoint_keys_without_secret_material():
    cfg = yaml.safe_load((HERE.parent / "config.yaml").read_text())
    keys = {e["group"] + "_" + e["name"] for e in cfg["external-endpoints"]}
    catalog = json.loads((HERE / "catalog.json").read_text())
    expected = [c["key"] for host in catalog.values() for c in host["checks"]]
    assert len(expected) == len(set(expected)) and set(expected) == keys
    assert all(
        e["token"].startswith("${GATUS_PUSH_") and e["heartbeat"]["interval"] == "3m" for e in cfg["external-endpoints"]
    )
    assert "Config.Env" not in collector.FORMAT and "State.Health.Log" not in collector.FORMAT


def test_push_rejects_redirect():
    with pytest.raises(RuntimeError, match="redirects forbidden"):
        collector.NoRedirect().redirect_request(None, None, None, None, None, None)


def test_swarm_retired_task_does_not_hide_active_replacement():
    check = {"swarm_service": "dokploy"}
    active = {"name": "/dokploy.1.new", "swarm_service": "dokploy", "status": "running", "health": "healthy"}
    retired = {**active, "name": "/dokploy.1.old", "status": "exited"}
    assert collector.evaluate(check, [active, retired])[0] is True
    assert collector.evaluate(check, [retired])[0] is False
    assert collector.evaluate(check, [active, {**active, "name": "/dokploy.2.new"}, retired])[0] is False
    assert collector.evaluate({"container": "dokploy.1.old"}, [retired])[0] is False


def test_admin_checks_cannot_follow_identity_provider_redirects():
    cfg = yaml.safe_load((HERE.parent / "config.yaml").read_text())
    endpoints = {e["name"]: e for e in cfg["endpoints"]}
    for name in ("dokploy", "legacy-hatchet"):
        assert endpoints[name]["client"]["ignore-redirect"] is True
        assert '[STATUS] == any(302, 401, 403)' in endpoints[name]["conditions"]
    mcp = endpoints["weknora-mcp-origin"]
    assert mcp["url"].endswith('/mcp/')
    assert '[STATUS] == 401' in mcp["conditions"]
    assert '[BODY].error == Unauthorized: missing authentication' in mcp["conditions"]
