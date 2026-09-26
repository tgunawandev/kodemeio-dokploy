"""Gatus config-as-code: lint (W10) + local forced-outage alert test (W9).

Run: ``uv run pytest ops/monitoring/gatus/tests -q``

Lint tests read the production ``config.yaml`` and need nothing else. The
end-to-end tests generate a test config FROM that production file (hostnames
rewritten to a fake target server, 5 s interval, thresholds 1, the
production telegram provider pointed at a local sink via ``api-url``) and run
the pinned Gatus image with docker compose; they skip only when docker is
unavailable.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

HERE = Path(__file__).resolve().parent
GATUS_DIR = HERE.parent
CONFIG = GATUS_DIR / "config.yaml"
PROD_COMPOSE = GATUS_DIR / "docker-compose.yml"
TEST_COMPOSE = HERE / "compose.test.yml"

FORBIDDEN_HOST_TOKENS = ("idtpp.com", "mandiriagro", "tpp")
GATE_GROUP = "admin-gates"


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


# ---------------------------------------------------------------------------
# Lint (W10)
# ---------------------------------------------------------------------------


def lint_violations(cfg: dict) -> list[str]:
    out: list[str] = []
    alerting = cfg.get("alerting") or {}
    for provider in ("telegram", "email"):
        if provider not in alerting:
            out.append(f"alerting.{provider} missing")
    tg = alerting.get("telegram") or {}
    if tg.get("token") != "${GATUS_TELEGRAM_TOKEN}" or tg.get("id") != "${GATUS_TELEGRAM_CHAT_ID}":
        out.append("telegram token/id must come from env (${GATUS_TELEGRAM_*}), never a literal")
    em = alerting.get("email") or {}
    if em.get("password") != "${GATUS_SMTP_PASS}":
        out.append("email password must come from env ${GATUS_SMTP_PASS}")
    if (cfg.get("storage") or {}).get("type") != "sqlite":
        out.append("storage.type must be sqlite")

    endpoints = cfg.get("endpoints") or []
    if not endpoints:
        out.append("no endpoints")
    names = [e.get("name") for e in endpoints]
    if len(names) != len(set(names)):
        out.append("duplicate endpoint names")
    for ep in endpoints:
        name = ep.get("name")
        types = {a.get("type") for a in ep.get("alerts") or []}
        if not {"telegram", "email"} <= types:
            out.append(f"{name}: alerts must include telegram AND email (has {sorted(types)})")
        host = urlsplit(ep.get("url", "")).hostname or ""
        if not host.endswith("kodeme.io"):
            out.append(f"{name}: host {host!r} is not a kodeme.io host")
        if any(tok in host for tok in FORBIDDEN_HOST_TOKENS):
            out.append(f"{name}: forbidden host {host!r}")
        if ep.get("group") == GATE_GROUP:
            if not (ep.get("client") or {}).get("ignore-redirect"):
                out.append(f"{name}: gate check must not follow redirects")
            if "[STATUS] == any(302, 401, 403)" not in ep.get("conditions", []):
                out.append(f"{name}: gate condition must be [STATUS] == any(302, 401, 403)")
    by_url = {ep.get("url"): ep for ep in endpoints}
    ready = by_url.get("https://llm.kodeme.io/health/readiness")
    if ready is None or "[BODY].db == connected" not in ready.get("conditions", []):
        out.append("llm readiness must assert [BODY].db == connected (spec F19)")
    for url in (
        "https://auth.kodeme.io/-/health/live/",
        "https://erp.kodeme.io/web/health",
        "https://hris.kodeme.io/web/health",
        "https://desk.kodeme.io/web/health",
        "https://mm.kodeme.io/api/v4/system/ping",
        "https://llm.kodeme.io/health/liveliness",
        "https://dokploy.kodeme.io/",
        "https://hatchet.kodeme.io/",
        "https://llm.kodeme.io/ui",
        "https://dsh.kodeme.io/",
    ):
        if url not in by_url:
            out.append(f"required endpoint missing: {url}")
    auth = by_url.get("https://auth.kodeme.io/-/health/live/") or {}
    if "[CERTIFICATE_EXPIRATION] > 336h" not in auth.get("conditions", []):
        out.append("auth.kodeme.io must carry the 14-day cert check")
    return out


def test_prod_config_lint_clean():
    assert lint_violations(load_config()) == []


def test_no_forbidden_hosts_anywhere_in_config_text():
    text = CONFIG.read_text().lower()
    for tok in ("idtpp", "mandiriagro"):
        assert tok not in text
    urls = re.findall(r"https?://[^\s\"']+", text)
    assert urls, "expected URLs in config"
    for u in urls:
        assert "tpp" not in (urlsplit(u).hostname or "")


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda c: c["endpoints"][0]["alerts"].pop(), "alerts must include telegram AND email"),
        (lambda c: c["alerting"].pop("email"), "alerting.email missing"),
        (
            lambda c: next(e for e in c["endpoints"] if e["name"] == "llm-readiness")["conditions"].remove(
                "[BODY].db == connected"
            ),
            "[BODY].db == connected",
        ),
        (
            lambda c: c["endpoints"].append({**c["endpoints"][0], "name": "x", "url": "https://erp.idtpp.com/"}),
            "forbidden host",
        ),
        (
            lambda c: next(e for e in c["endpoints"] if e["group"] == GATE_GROUP).pop("client"),
            "must not follow redirects",
        ),
        (lambda c: c["alerting"]["telegram"].update(token="123:literal"), "never a literal"),
    ],
)
def test_lint_has_teeth(mutate, expect):
    cfg = copy.deepcopy(load_config())
    mutate(cfg)
    assert any(expect in v for v in lint_violations(cfg)), lint_violations(cfg)


def test_prod_compose_shape():
    comp = yaml.safe_load(PROD_COMPOSE.read_text())
    svcs = comp["services"]
    assert set(svcs) == {"gatus", "heartbeat"}
    for name, svc in svcs.items():
        assert ":" in svc["image"] and not svc["image"].endswith(":latest"), name
        assert "ports" not in svc, f"{name} must not publish ports"
        assert svc["restart"] == "unless-stopped"
        assert svc["deploy"]["resources"]["limits"]["cpus"]
        assert svc["deploy"]["resources"]["limits"]["memory"]
        assert svc["networks"] == ["dokploy-network"]
        assert "labels" not in svc, "no Traefik router by default (G7)"
    assert svcs["gatus"]["image"] == "ghcr.io/twin/gatus:v5.37.0"
    assert "./config.yaml:/config/config.yaml:ro" in svcs["gatus"]["volumes"]
    assert "gatus-data:/data" in svcs["gatus"]["volumes"]
    assert "healthcheck" in svcs["heartbeat"]
    assert comp["networks"]["dokploy-network"]["external"] is True


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def test_prod_compose_renders_with_dummy_env():
    if not _docker_ok():
        pytest.skip("docker unavailable")
    env = {
        **os.environ,
        "GATUS_TELEGRAM_TOKEN": "t",
        "GATUS_TELEGRAM_CHAT_ID": "1",
        "GATUS_SMTP_USER": "u",
        "GATUS_SMTP_PASS": "p",
        "GATUS_ALERT_TO": "a@example.invalid",
        "HC_GATUS_URL": "https://hc-ping.example.invalid/x",
    }
    r = subprocess.run(
        ["docker", "compose", "-f", str(PROD_COMPOSE), "config", "--quiet"], env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    # and the :? guards really guard
    env.pop("HC_GATUS_URL")
    r = subprocess.run(
        ["docker", "compose", "-f", str(PROD_COMPOSE), "config", "--quiet"], env=env, capture_output=True, text=True
    )
    assert r.returncode != 0 and "HC_GATUS_URL" in r.stderr


# ---------------------------------------------------------------------------
# Test-config generation (from the production file)
# ---------------------------------------------------------------------------


def make_test_config(cfg: dict) -> dict:
    t = copy.deepcopy(cfg)
    t["storage"] = {"type": "memory"}
    tg = t["alerting"]["telegram"]
    tg["api-url"] = "http://alert-sink:9000"
    tg["default-alert"].update({"failure-threshold": 1, "success-threshold": 1})
    del t["alerting"]["email"]
    for ep in t["endpoints"]:
        u = urlsplit(ep["url"])
        ep["url"] = f"http://fake-targets:8000/{u.hostname}{u.path or '/'}"
        ep["interval"] = "5s"
        ep["enabled"] = True
        ep["conditions"] = [c for c in ep["conditions"] if "CERTIFICATE_EXPIRATION" not in c]
        ep["alerts"] = [a for a in ep["alerts"] if a["type"] != "email"]
    return t


def test_make_test_config_keeps_prod_conditions():
    prod = load_config()
    test = make_test_config(prod)
    assert len(test["endpoints"]) == len(prod["endpoints"])
    for p, t in zip(prod["endpoints"], test["endpoints"], strict=True):
        assert t["name"] == p["name"]
        assert t["conditions"] == [c for c in p["conditions"] if "CERTIFICATE" not in c]
        assert t.get("client") == p.get("client")
        assert t["url"].startswith("http://fake-targets:8000/")


# ---------------------------------------------------------------------------
# End-to-end (W9)
# ---------------------------------------------------------------------------


def _http(url: str, data: dict | None = None):
    req = urllib.request.Request(url, method="POST" if data is not None else "GET")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, body, timeout=5) as r:
        return json.loads(r.read() or b"null")


class Stack:
    def __init__(self, tmp: Path):
        self.project = f"gatustest{uuid.uuid4().hex[:8]}"
        self.cfg_path = tmp / "gatus-test-config.yaml"
        self.cfg_path.write_text(yaml.safe_dump(make_test_config(load_config()), sort_keys=False))
        self.env = {**os.environ, "GATUS_TEST_CONFIG": str(self.cfg_path)}

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose", "-p", self.project, "-f", str(TEST_COMPOSE), *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
        )

    def port(self, svc: str, p: int) -> str:
        return self.compose("port", svc, str(p)).stdout.strip()

    def up(self):
        self.compose("up", "-d")
        self.fake = f"http://{self.port('fake-targets', 8000)}"
        self.sink = f"http://{self.port('alert-sink', 9000)}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                _http(self.fake + "/_control")
                _http(self.sink + "/_records")
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("fake-targets/alert-sink did not come up")

    def down(self):
        self.compose("down", "-v", "--remove-orphans", check=False)

    def records(self) -> list[dict]:
        return _http(self.sink + "/_records")

    def telegram(self) -> list[dict]:
        out = []
        for r in self.records():
            if r["method"] == "POST" and r["path"] == "/bot000000:TEST-TOKEN/sendMessage":
                out.append({**r, "json": json.loads(r["body"])})
        return out


def wait_for(pred, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(1)
    raise AssertionError(f"timed out after {timeout}s waiting for: {what}")


def _alert(stack: Stack, endpoint: str, kind: str) -> list[dict]:
    return [
        m
        for m in stack.telegram()
        if f"*{endpoint}*" in m["json"]["text"] or f"/{endpoint}*" in m["json"]["text"]
        if f"has been {kind}" in m["json"]["text"]
    ]


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not _docker_ok():
        pytest.skip("docker unavailable")
    s = Stack(tmp_path_factory.mktemp("gatus"))
    try:
        s.up()
        yield s
    finally:
        s.down()


def test_e2e_healthy_baseline_sends_no_alert(stack):
    # Let every endpoint run at least three 5 s rounds against healthy fakes.
    time.sleep(16)
    msgs = stack.telegram()
    assert msgs == [], [m["json"]["text"] for m in msgs]
    # ...while Gatus really is evaluating them (fake targets see traffic)
    assert _http(stack.fake + "/_control")["db"] == "connected"


def test_e2e_llm_db_disconnected_alerts_then_resolves(stack):
    _http(stack.fake + "/_control", {"db": "disconnected"})
    trig = wait_for(lambda: _alert(stack, "llm-readiness", "triggered"), 30, "llm-readiness triggered")
    assert trig[0]["json"]["chat_id"] == "4242"
    assert "[BODY].db (disconnected) == connected" in trig[0]["json"]["text"]
    # only the readiness check fails — liveliness still says 200
    assert not _alert(stack, "llm-liveliness", "triggered")
    _http(stack.fake + "/_control", {"db": "connected"})
    wait_for(lambda: _alert(stack, "llm-readiness", "resolved"), 30, "llm-readiness resolved")


def test_e2e_open_admin_gate_alerts(stack):
    _http(stack.fake + "/_control", {"open_gates": ["dokploy.kodeme.io"]})
    wait_for(lambda: _alert(stack, "gate-dokploy", "triggered"), 30, "gate-dokploy triggered")
    assert not _alert(stack, "gate-dsh", "triggered"), "a closed gate must not alert"
    _http(stack.fake + "/_control", {"open_gates": []})
    wait_for(lambda: _alert(stack, "gate-dokploy", "resolved"), 30, "gate-dokploy resolved")


def test_e2e_heartbeat_pings_then_fails_when_gatus_dies(stack):
    wait_for(
        lambda: [r for r in stack.records() if r["method"] == "GET" and r["path"] == "/hc/gatus"],
        20,
        "heartbeat success ping",
    )
    assert not [r for r in stack.records() if r["path"] == "/hc/gatus/fail"]
    stack.compose("stop", "gatus")
    wait_for(
        lambda: [r for r in stack.records() if r["path"] == "/hc/gatus/fail"],
        30,
        "heartbeat /fail ping after gatus stopped",
    )
    logs = stack.compose("logs", "heartbeat").stdout
    assert "alert-sink:9000/hc" not in logs, "heartbeat must never print its ping URL"
