"""Verify trusted Dokploy readback BEFORE triggering the operator rollout.

This is a bounded operator check, not an identity provider or agent release
adapter. Callers obtain live/snapshot records through the authenticated front
door. Credentials remain private and are never returned or included in errors.
"""

import re
import shlex

COMPOSE = "OaeHlGC5BvVH6R3W-rh9q"
SERVER = "3aceAeKfqzyFWTppqDAlI"
PROJECT = "compose-program-optical-panel-vswhbh"


def assert_staged(live, manifest, snapshot):
    """Fail if source, command, target, provider secret or project drifted."""
    for key, value in {
        "composeId": COMPOSE,
        "serverId": SERVER,
        "appName": PROJECT,
        "environmentId": snapshot["environmentId"],
        "autoDeploy": False,
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
    if not re.fullmatch(
        r"ghcr.io/tgunawandev/kodemeio-omp@sha256:[a-f0-9]{64}", manifest["env_overrides"].get("OMP_IMAGE", "")
    ):
        raise ValueError("Immutable approved image required")

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
