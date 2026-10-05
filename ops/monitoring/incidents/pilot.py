#!/usr/bin/env python3
"""Install/test the isolated local monitoring pilot; never calls production."""

import argparse
import fcntl
import json
import os
import secrets
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env.local"


def values():
    names = (
        "INCIDENT_GATUS_TOKEN",
        "INCIDENT_GLITCHTIP_TOKEN",
        "INCIDENT_READ_TOKEN",
        "INCIDENT_GLITCHTIP_READ_TOKEN",
        "PILOT_DB_PASSWORD",
        "PILOT_DJANGO_SECRET",
        "PILOT_OPERATOR_PASSWORD",
    )
    with os.fdopen(os.open(ENV, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if os.fstat(stream.fileno()).st_mode & 0o077:
            raise ValueError("pilot env file must have mode 0600")
        raw = stream.read()
        env = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        missing = {name: secrets.token_hex(32) for name in names if name not in env}
        if missing:
            stream.write(
                ("\n" if raw and not raw.endswith("\n") else "")
                + "\n".join(f"{key}={value}" for key, value in missing.items())
                + "\n"
            )
            env.update(missing)
        return env


def require_local_docker():
    docker_host = os.environ.get("DOCKER_HOST")
    if not docker_host:
        docker_host = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    if not docker_host.startswith("unix://"):
        raise ValueError("local pilot requires a local Docker socket; remote Docker contexts are refused")


def compose(*args, **kwargs):
    require_local_docker()
    return subprocess.run(
        ["docker", "compose", "--env-file", str(ENV), "-f", str(ROOT / "compose.local.yml"), *args],
        cwd=ROOT,
        check=True,
        text=True,
        **kwargs,
    )


def request(url, body=None, token=None, *, timeout=10):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(128 * 1024)
        return json.loads(raw) if raw else None


def wait_for(predicate, label, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(2)
    raise RuntimeError(f"pilot timed out: {label}")


def seed():
    result = compose(
        "exec",
        "-T",
        "glitchtip",
        "python",
        "manage.py",
        "shell",
        "-c",
        (ROOT / "bootstrap.local.py").read_text(),
        capture_output=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


def test(env):
    seeded = seed()
    rows_url = "http://127.0.0.1:8788/incidents"
    token = env["INCIDENT_READ_TOKEN"]
    before = request(rows_url, token=token)
    old_generation = max((r["generation"] for r in before if r["source"] == "gatus"), default=0)
    # Ensure baseline first, then allow Gatus to observe recovery before failure.
    request("http://127.0.0.1:8787/recover", b"")
    time.sleep(12)
    request("http://127.0.0.1:8787/fail", b"")
    failed = wait_for(
        lambda: next(
            (
                r
                for r in request(rows_url, token=token)
                if r["source"] == "gatus" and r["state"] == "open" and r["generation"] > old_generation
            ),
            None,
        ),
        "Gatus failure",
    )
    request("http://127.0.0.1:8787/recover", b"")
    recovered = wait_for(
        lambda: next(
            (
                r
                for r in request(rows_url, token=token)
                if r["id"] == failed["id"]
                and r["state"] == "recovered"
                and r["collected_generation"] == r["generation"]
            ),
            None,
        ),
        "Gatus recovery evidence",
    )
    event = {
        "event_id": uuid.uuid4().hex,
        "platform": "python",
        "level": "error",
        "timestamp": time.time(),
        "environment": "local",
        "release": "synthetic-pilot",
        "fingerprint": ["incident-pilot", uuid.uuid4().hex],
        "exception": {
            "values": [
                {
                    "type": "SyntheticPilotError",
                    "value": "SYNTHETIC LOCAL TEST: incident delivery, not an application defect",
                    "stacktrace": {
                        "frames": [{"filename": "synthetic-pilot.py", "function": "controlled_error", "lineno": 1}]
                    },
                }
            ]
        },
    }
    request(
        f"http://127.0.0.1:8789/api/{seeded['project_id']}/store/?sentry_key={seeded['public_key']}&sentry_version=7",
        event,
    )
    old_ids = {r["id"] for r in before}
    issue = wait_for(
        lambda: next(
            (
                r
                for r in request(rows_url, token=token)
                if r["source"] == "glitchtip"
                and r["service"] == "kodemeio-web-local"
                and r["id"] not in old_ids
                and r["collected_generation"] == r["generation"]
            ),
            None,
        ),
        "GlitchTip webhook and stack trace",
        timeout=240,
    )
    bundle = request(f"{rows_url}/{issue['id']}", token=token)
    assert any(e["kind"] == "exception" and e["type"] == "SyntheticPilotError" for e in bundle["evidence"])
    output = ROOT / "evidence"
    output.mkdir(exist_ok=True, mode=0o700)
    bundle_file = output / "synthetic-incident.json"
    bundle_file.write_text(json.dumps(bundle, indent=2) + "\n")
    evidence = {
        "synthetic": True,
        "gatus_failure": failed["id"],
        "gatus_recovery_generation": recovered["generation"],
        "glitchtip_issue": issue["id"],
        "stack_trace": True,
        "agent_dispatch": False,
        "bundle_file": str(bundle_file),
        "production_touched": False,
    }
    (output / "acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


def main():
    os.umask(0o077)
    require_local_docker()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "test", "status", "stop", "login-details"))
    args = parser.parse_args()
    env = values()
    if args.command == "login-details":
        print(
            json.dumps(
                {
                    "url": "http://127.0.0.1:8789",
                    "email": "operator-local@example.invalid",
                    "password_file": str(ENV),
                    "password_key": "PILOT_OPERATOR_PASSWORD",
                },
                indent=2,
            )
        )
    elif args.command == "install":
        compose("config", "--quiet")
        compose("up", "-d", "--build", "--wait", "--wait-timeout", "240")
        seed()
        print("Local pilot ready: Gatus 127.0.0.1:8786; receiver 127.0.0.1:8788; GlitchTip 127.0.0.1:8789")
    elif args.command == "test":
        test(env)
    elif args.command == "status":
        compose("ps")
    else:
        compose("stop")  # keep all evidence and databases for inspection


if __name__ == "__main__":
    main()
