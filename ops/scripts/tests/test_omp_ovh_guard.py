"""Non-production regressions for exact-artifact OMP host migration."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ops/scripts"))
spec = importlib.util.spec_from_file_location("omp_ovh_guard", ROOT / "ops/scripts/omp_ovh_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def staged():
    manifest = json.loads((ROOT / "deploys/instances/production/kod-infra-omp.yaml").read_text())
    old = {"env": "DEEPSEEK_API_KEY=fixture-only\nTZ=Asia/Jakarta"}
    live = {
        "composeId": guard.COMPOSE,
        "serverId": guard.SERVER,
        "environmentId": guard.ENVIRONMENT,
        "appName": guard.APP_NAME,
        "autoDeploy": False,
        "createEnvFile": True,
        "sourceType": "github",
        "repository": "kodemeio-omp",
        "owner": "tgunawandev",
        "branch": guard.BRANCH,
        "composePath": "docker-compose.prod.yml",
        "githubId": guard.GITHUB,
        "command": manifest["command"],
        "env": old["env"] + "\n" + "\n".join(f"{k}={v}" for k, v in manifest["env_overrides"].items()),
    }
    return live, manifest, old


def test_new_control_plane_identity_preserves_volume_namespace():
    live, manifest, old = staged()
    proof = guard.assert_staged(live, manifest, old)
    assert proof["volume_project"] != live["appName"]
    assert proof["volume_project"] == "compose-program-optical-panel-vswhbh"


@pytest.mark.parametrize(
    "key,value",
    [
        ("serverId", "old-server"),
        ("composeId", "old-compose"),
        ("environmentId", "another-tenant"),
        ("appName", "another-app"),
        ("autoDeploy", True),
        ("createEnvFile", False),
        ("sourceType", "raw"),
        ("githubId", "another-provider"),
        ("owner", "another-owner"),
        ("command", "compose up -d --build"),
    ],
)
def test_live_scope_drift_is_refused(key, value):
    live, manifest, old = staged()
    live[key] = value
    with pytest.raises(ValueError, match="mismatch"):
        guard.assert_staged(live, manifest, old)


@pytest.mark.parametrize(
    "key,value",
    [
        ("OMP_IMAGE", "ghcr.io/tgunawandev/kodemeio-omp@sha256:" + "a" * 64),
        ("OMP_BUILD_COMMIT", "a" * 40),
        ("OMP_MEMORY_LIMIT", "768m"),
        ("OMP_CPU_LIMIT", "6"),
        ("OMP_PRESET", "hybrid"),
        ("GH_TOKEN", "fixture-not-a-secret"),
    ],
)
def test_joint_manifest_and_live_environment_drift_is_refused(key, value):
    live, manifest, old = staged()
    manifest["env_overrides"][key] = value
    live["env"] = old["env"] + "\n" + "\n".join(f"{k}={v}" for k, v in manifest["env_overrides"].items())
    with pytest.raises(ValueError, match="artifact and resource"):
        guard.assert_staged(live, manifest, old)


def test_changed_provider_secret_is_refused_without_echo():
    live, manifest, old = staged()
    live["env"] = live["env"].replace("fixture-only", "fixture-changed")
    with pytest.raises(ValueError, match="environment drift") as error:
        guard.assert_staged(live, manifest, old)
    assert "fixture" not in str(error.value)


def test_wrong_declared_destination_is_refused():
    live, manifest, old = staged()
    manifest["server"] = "kod-hzc-01"
    with pytest.raises(ValueError, match="destination"):
        guard.assert_staged(live, manifest, old)
