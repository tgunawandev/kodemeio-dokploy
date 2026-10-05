import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_local_and_cloud_collector_credential_boundaries():
    local = yaml.safe_load((ROOT / "compose.local.yml").read_text())
    cloud = yaml.safe_load((ROOT / "compose.worker.yml").read_text())
    for stack in (local, cloud):
        ingress = stack["services"]["incident-ingress"]
        env = ingress["environment"]
        assert "INCIDENT_GLITCHTIP_READ_TOKEN" not in env
        assert "HATCHET_CLIENT_TOKEN" not in env
        assert ingress["read_only"] and ingress["cap_drop"] == ["ALL"]
        for svc in stack["services"].values():
            assert not any(
                key.startswith(("DEEPSEEK_", "GITHUB_", "FRIDAY_RUNNER_")) for key in svc.get("environment", {})
            )
    assert local["services"]["gatus"]["image"] == "ghcr.io/twin/gatus:v5.37.0"
    assert local["services"]["glitchtip"]["image"].startswith("glitchtip/glitchtip:6.2.6@sha256:")
    for svc in local["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in svc.get("ports", []))
    assert not cloud["services"]["incident-worker"].get("ports")


def test_gatus_uses_reference_only_authenticated_recovery_payload():
    cfg = json.loads((ROOT / "gatus.local.json").read_text())
    custom = cfg["alerting"]["custom"]
    assert custom["headers"]["Authorization"] == "Bearer ${INCIDENT_GATUS_TOKEN}"
    assert json.loads(custom["body"]) == {"endpoint": "[ENDPOINT_NAME]", "state": "[ALERT_TRIGGERED_OR_RESOLVED]"}
    assert custom["default-alert"]["failure-threshold"] == 3
    assert custom["default-alert"]["success-threshold"] == 2
    assert custom["default-alert"]["send-on-resolved"]
    service = json.loads((ROOT / "services.local.json").read_text())["kodemeio-web-local"]
    assert service["environment"] == "local" and service["synthetic"]


def test_cloud_compose_renders_without_real_secrets():
    env = {
        **os.environ,
        "INCIDENT_IMAGE": "example.invalid/collector@sha256:" + "a" * 64,
        "INCIDENT_GATUS_TOKEN": "g" * 32,
        "INCIDENT_GLITCHTIP_TOKEN": "t" * 32,
        "INCIDENT_READ_TOKEN": "r" * 32,
        "INCIDENT_GLITCHTIP_READ_TOKEN": "e" * 32,
        "HATCHET_CLIENT_TOKEN": "synthetic",
        "HATCHET_CLIENT_HOST_PORT": "engine.example.invalid:443",
        "HATCHET_CLIENT_SERVER_URL": "https://hatchet.example.invalid",
    }
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.worker.yml"),
            "config",
            "--quiet",
        ],
        env=env,
        check=True,
        capture_output=True,
    )


def test_cloud_monitoring_candidate_preserves_local_versions_and_credential_boundaries():
    local = yaml.safe_load((ROOT / "compose.local.yml").read_text())
    cloud = yaml.safe_load((ROOT / "compose.cloud.yml").read_text())
    for name in ("gatus", "glitchtip", "glitchtip-db"):
        assert cloud["services"][name]["image"] == local["services"][name]["image"]
    assert cloud["services"]["incident-collector"]["command"] == local["services"]["incident-collector"]["command"]
    assert cloud["services"]["heartbeat"]["environment"]["HC_GATUS_URL"]
    assert "PILOT_OPERATOR_PASSWORD" not in cloud["services"]["glitchtip"]["environment"]
    assert "INCIDENT_GLITCHTIP_READ_TOKEN" not in cloud["services"]["incident-ingress"]["environment"]
    for service in cloud["services"].values():
        assert not service.get("ports")
        assert not any(
            key.startswith(("DEEPSEEK_", "GITHUB_", "KCTL_ODOO", "HATCHET_")) for key in service.get("environment", {})
        )
    keys = (
        "INCIDENT_IMAGE",
        "INCIDENT_GATUS_TOKEN",
        "INCIDENT_GLITCHTIP_TOKEN",
        "INCIDENT_READ_TOKEN",
        "INCIDENT_GLITCHTIP_READ_TOKEN",
        "GATUS_TELEGRAM_TOKEN",
        "GATUS_TELEGRAM_CHAT_ID",
        "GATUS_SMTP_USER",
        "GATUS_SMTP_PASS",
        "GATUS_ALERT_TO",
        "HC_GATUS_URL",
        "GLITCHTIP_DB_PASSWORD",
        "GLITCHTIP_SECRET_KEY",
        "GLITCHTIP_DOMAIN",
        "GLITCHTIP_ALLOWED_HOSTS",
        "GLITCHTIP_EMAIL_URL",
    )
    env = {**os.environ, **dict.fromkeys(keys, "synthetic-placeholder")}
    subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.cloud.yml"), "config", "--quiet"],
        env=env,
        check=True,
        capture_output=True,
    )
