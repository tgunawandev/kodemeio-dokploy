"""Read-only checks for the bounded, founder-authorized OMP host move.

This consumes trusted operator readback, not agent assertions or approval
credentials. It does not authenticate callers or grant deployment authority.
The operator must resolve the pending data choice and enforce human approval
before invoking the deployment front door. Legacy rollout evidence stays intact.
"""

import shlex

from omp_rollout_guard import BRANCH, IMAGE, PROJECT, SOURCE

COMPOSE = "QNgRohOENxsbnmFdGhJoC"
APP_NAME = "compose-override-online-protocol-4e2bjl"
SERVER = "W-sYqWjxLu9cAhp41pwpA"
ENVIRONMENT = "k0aSx0yqY5o3brJYjtaw3"
GITHUB = "ZieFEXD90Ai1X6U6wj0ur"


def assert_staged(live, manifest, previous):
    """Reject target, artifact, capacity, source or environment drift."""
    if (manifest["project"], manifest["environment"], manifest["server"]) != (
        "apps",
        "production",
        "kod-ovh-01",
    ):
        raise ValueError("Approved OVH destination required")
    if manifest.get("auto_deploy") is not False:
        raise ValueError("Automatic deployment must stay disabled")
    source = manifest["source"]
    if source != {
        "type": "github",
        "owner": "tgunawandev",
        "repo": "kodemeio-omp",
        "branch": BRANCH,
        "compose_path": "docker-compose.prod.yml",
    }:
        raise ValueError("Exact reviewed source required")
    expected_command = [
        "compose",
        "-p",
        PROJECT,
        "-f",
        "docker-compose.prod.yml",
        "up",
        "-d",
        "--no-build",
        "--pull",
        "never",
        "--remove-orphans",
    ]
    if shlex.split(manifest["command"]) != expected_command:
        raise ValueError("Approved prebuilt-only volume-preserving command required")
    for key, value in {
        "composeId": COMPOSE,
        "appName": APP_NAME,
        "serverId": SERVER,
        "environmentId": ENVIRONMENT,
        "autoDeploy": False,
        "createEnvFile": True,
        "sourceType": "github",
        "repository": "kodemeio-omp",
        "owner": "tgunawandev",
        "branch": BRANCH,
        "composePath": "docker-compose.prod.yml",
        "githubId": GITHUB,
        "command": manifest["command"],
    }.items():
        if live.get(key) != value:
            raise ValueError("Staged OVH configuration mismatch: " + key)
    overrides = manifest["env_overrides"]
    required = {
        "OMP_IMAGE": IMAGE,
        "OMP_BUILD_COMMIT": SOURCE,
        "OMP_IMAGE_TAG": SOURCE,
        "OMP_MEMORY_LIMIT": "3g",
        "OMP_CPU_LIMIT": "2",
        "OMP_CONTAINER_PREFIX": "kod-infra-omp",
        "OMP_PRESET": "deepseek",
        "TZ": "Asia/Jakarta",
    }
    if overrides != required:
        raise ValueError("Exact reviewed artifact and resource budget required")

    def parse(text):
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))

    before = parse(previous["env"])
    if not before.get("DEEPSEEK_API_KEY") or parse(live["env"]) != before | required:
        raise ValueError("Provider/environment drift; no credentials logged")
    return {
        "compose_id": COMPOSE,
        "server_id": SERVER,
        "volume_project": PROJECT,
        "image": IMAGE,
        "source_sha": SOURCE,
        "resource_budget_verified": True,
        "environment_preserved": True,
    }
