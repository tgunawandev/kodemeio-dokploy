"""Guard production terminal promotion against rebuilds and new agent authority."""

import json
import re
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return yaml.safe_load((ROOT / "deploys/instances/production/kod-infra-omp.yaml").read_text())


def test_production_requires_an_immutable_prebuilt_image():
    release = manifest()
    assert re.fullmatch(r"ghcr.io/tgunawandev/kodemeio-omp@sha256:[a-f0-9]{64}", release["env_overrides"]["OMP_IMAGE"])
    command = shlex.split(release["command"])
    assert command[:3] == ["compose", "-p", "compose-program-optical-panel-vswhbh"]
    assert command[3:7] == ["-f", "docker-compose.prod.yml", "up", "-d"]
    assert command[7:] == ["--no-build", "--pull", "never", "--remove-orphans"]
    assert "--build" not in command


def test_rollout_preserves_the_existing_target_and_provider():
    release = manifest()
    assert release["instance"]["name"] == "kod-infra-omp"
    assert (release["project"], release["environment"], release["server"]) == ("apps", "production", "kod-ovh-01")
    assert release["env_overrides"]["OMP_PRESET"] == "deepseek"
    assert release["env_file"] == "../../env/production/.env.kod-infra-omp"
    assert not any("KEY" in key or "TOKEN" in key or "PASSWORD" in key for key in release["env_overrides"])


def test_source_and_automatic_deployment_are_not_floating():
    release = manifest()
    assert release["auto_deploy"] is False
    sha = release["env_overrides"]["OMP_BUILD_COMMIT"]
    assert re.fullmatch("[a-f0-9]{40}", sha)
    assert release["source"]["branch"] == "release/omp-agent-boundaries-" + sha[:7]
    assert release["env_overrides"]["OMP_IMAGE_TAG"] == sha


def test_terminal_profiles_do_not_gain_production_or_publication_authority():
    profiles = [
        json.loads((ROOT / f"contracts/coding_agents/{name}-omp.json").read_text()) for name in ("friday", "veronica")
    ]
    assert all(profile["production_access"] is False for profile in profiles)
    assert profiles[0]["principal"] != profiles[1]["principal"]
    assert profiles[1]["publication"] == "none"


def test_readiness_matches_the_promoted_source_and_image_without_inherited_database_backup():
    release = manifest()
    evidence = json.loads((ROOT / "ops/evidence/omp-production-readiness-2026-10-05.json").read_text())
    assert release["env_overrides"]["OMP_IMAGE"] == evidence["registry_image"]
    assert release["env_overrides"]["OMP_BUILD_COMMIT"] == evidence["source_sha"]
    assert evidence["canary"]["image_id"] == evidence["tested_image_id"]
    assert "extends" not in release and "backup" not in release
