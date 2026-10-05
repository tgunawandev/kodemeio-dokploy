import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / "idtpp"


def test_production_services_bind_legacy_project_ids_and_keep_real_faults_separate():
    services = json.loads((ROOT / "services.json").read_text())
    projects = [cfg for cfg in services.values() if cfg.get("glitchtip")]
    assert len(projects) == 9
    assert {p["glitchtip"]["project_id"] for p in projects} == set(range(1, 10))
    for name, cfg in services.items():
        if cfg.get("glitchtip"):
            assert cfg["glitchtip"]["issue_api"] == "legacy"
            assert cfg["glitchtip"]["org"] == "idtpp"
        if not cfg.get("synthetic"):
            assert "ticket" not in cfg  # never misfile every real defect under the rollout ticket
        if name.endswith("-stg"):
            assert cfg["environment"] == "staging"


def test_cloud_mounts_preserve_read_only_runtime_and_route_only_glitchtip_ingest():
    stack = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name in ("incident-ingress", "incident-collector"):
        svc = stack["services"][name]
        assert svc["read_only"] and svc["cap_drop"] == ["ALL"]
        assert svc["pull_policy"] == "never" and svc["image"].startswith("sha256:")
        assert not svc.get("ports")
        assert not any(k.startswith(("KCTL_", "GITHUB_", "DEEPSEEK_", "HATCHET_")) for k in svc["environment"])
    assert stack["services"]["incident-ingress"]["image"] == stack["services"]["incident-collector"]["image"]
    for cfg in stack["configs"].values():
        assert cfg["file"].startswith("/etc/dokploy/monitoring/tpp-infra-gatus/")
        assert "content" not in cfg  # Docker Compose refuses inline content in read-only containers
    labels = stack["services"]["incident-ingress"]["labels"]
    assert "PathPrefix(`/webhooks/glitchtip/`)" in labels["traefik.http.routers.tpp-incident-webhook.rule"]
    assert labels["traefik.http.routers.tpp-incident-webhook.observability.accesslogs"] == "false"
    assert labels["traefik.http.routers.tpp-incident-webhook.observability.tracing"] == "false"
    assert "INCIDENT_GLITCHTIP_READ_TOKEN" not in stack["services"]["incident-ingress"]["environment"]
