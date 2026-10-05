"""Verify trusted Dokploy readback BEFORE triggering the operator rollout.

This is a bounded operator check, not an identity provider or agent release
adapter. Callers obtain live/snapshot records through the authenticated front
door. Credentials remain private and are never returned or included in errors.
"""

import shlex

COMPOSE = "OaeHlGC5BvVH6R3W-rh9q"
SERVER = "3aceAeKfqzyFWTppqDAlI"
PROJECT = "compose-program-optical-panel-vswhbh"
# This bounded rollout is tied to the reviewed artifact, not arbitrary desired state.
SOURCE = "6779db2e6be05d01d04e00c6b4e3db59f0c21239"
BRANCH = "release/omp-agent-boundaries-6779db2"
IMAGE = "ghcr.io/tgunawandev/kodemeio-omp@sha256:aac0a855a0e95efb0054d29bef89d01f3cf5e1df0f6f79d3bdddaf6a80ea5163"


def assert_staged(live, manifest, snapshot):
    """Fail if source, command, target, provider secret or project drifted."""
    for key, value in {
        "composeId": COMPOSE,
        "serverId": SERVER,
        "appName": PROJECT,
        "environmentId": snapshot["environmentId"],
        "autoDeploy": False,
        "createEnvFile": True,
        "sourceType": "github",
        "repository": "kodemeio-omp",
        "owner": "tgunawandev",
        "branch": manifest["source"]["branch"],
        "composePath": "docker-compose.prod.yml",
        "command": manifest["command"],
    }.items():
        if live.get(key) != value:
            raise ValueError("Dokploy staged configuration mismatch: " + key)
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
    if shlex.split(live["command"]) != expected_command:
        raise ValueError("Approved prebuilt-only command required")
    if manifest["source"]["branch"] != BRANCH:
        raise ValueError("Approved source branch required")
    overrides = manifest["env_overrides"]
    if overrides.get("OMP_IMAGE") != IMAGE:
        raise ValueError("Exact reviewed image required")
    if overrides.get("OMP_BUILD_COMMIT") != SOURCE or overrides.get("OMP_IMAGE_TAG") != SOURCE:
        raise ValueError("Exact reviewed source revision required")

    def parse(text):
        # Dokploy receives normalized KEY=value content from this manifest.
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))

    previous = parse(snapshot["env"])
    expected = previous | manifest["env_overrides"]
    if parse(live["env"]) != expected:
        raise ValueError("Staged environment drift; no credentials logged")
    if not previous.get("DEEPSEEK_API_KEY") or "DEEPSEEK_API_KEY" in manifest["env_overrides"]:
        raise ValueError("Existing provider secret must be preserved")
    return {
        "compose_id": COMPOSE,
        "server_id": SERVER,
        "command_verified": True,
        "environment_preserved": True,
        "image": manifest["env_overrides"]["OMP_IMAGE"],
    }
