"""Non-production staged-readback and front-door refusal regressions."""

import copy
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("omp_rollout_guard", ROOT / "ops/scripts/omp_rollout_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def staged():
    manifest = json.loads((ROOT / "deploys/instances/production/kod-infra-omp.yaml").read_text())
    snapshot = {"environmentId": "fixture-production-env", "env": "DEEPSEEK_API_KEY=fixture-only\nTZ=Asia/Jakarta"}
    live = {
        "composeId": guard.COMPOSE,
        "serverId": guard.SERVER,
        "appName": guard.PROJECT,
        "environmentId": snapshot["environmentId"],
        "autoDeploy": False,
        "sourceType": "github",
        "repository": "kodemeio-omp",
        "owner": "tgunawandev",
        "branch": manifest["source"]["branch"],
        "composePath": "docker-compose.prod.yml",
        "command": manifest["command"],
        "env": snapshot["env"] + "\n" + "\n".join(f"{key}={value}" for key, value in manifest["env_overrides"].items()),
    }
    return live, manifest, snapshot


def test_exact_staged_readback_preserves_provider_and_target():
    live, manifest, snapshot = staged()
    assert guard.assert_staged(live, manifest, snapshot)["command_verified"] is True


@pytest.mark.parametrize(
    "key,value",
    [
        ("command", ""),
        ("autoDeploy", True),
        ("appName", "new-project"),
        ("composeId", "another-compose"),
        ("serverId", "another-host"),
        ("environmentId", "fixture-staging-env"),
        ("branch", "main"),
        ("sourceType", "raw"),
        ("owner", "another-tenant"),
        ("repository", "another-app"),
    ],
)
def test_drift_blocks_before_any_deployment(key, value):
    live, manifest, snapshot = staged()
    live[key] = value
    with pytest.raises(ValueError, match="mismatch"):
        guard.assert_staged(live, manifest, snapshot)


def test_changed_provider_secret_fails_without_exposing_values():
    live, manifest, snapshot = staged()
    live["env"] = live["env"].replace("fixture-only", "fixture-changed")
    with pytest.raises(ValueError, match="environment drift") as error:
        guard.assert_staged(live, manifest, snapshot)
    assert "fixture" not in str(error.value)


def test_joint_manifest_and_live_command_change_is_refused():
    live, manifest, snapshot = staged()
    manifest = copy.deepcopy(manifest)
    live["command"] = manifest["command"] = "compose up -d --build"
    with pytest.raises(ValueError, match="prebuilt-only"):
        guard.assert_staged(live, manifest, snapshot)


@pytest.mark.parametrize("verb", ["setup", "run", "post"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_write_verbs_require_door_confirmation_without_calling_real_cli(tmp_path, verb, confirmed):
    fake = tmp_path / "kctl-dokploy"
    marker = tmp_path / "calls"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$OMP_TEST_CALLS"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], OMP_TEST_CALLS=str(marker))
    result = subprocess.run(
        [str(ROOT / "dokploy.sh"), "kodemeio", "deploy", verb, "-f", "fixture.yaml", *(["--yes"] if confirmed else [])],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = marker.read_text() if marker.exists() else ""
    if confirmed:
        assert result.returncode == 0
        assert f"deploy {verb} -f fixture.yaml" in calls
        assert "--yes" not in calls
    else:
        assert result.returncode == 2
        assert "Re-run with --yes" in result.stderr
        assert f"deploy {verb}" not in calls
