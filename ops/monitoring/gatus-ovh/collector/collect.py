#!/usr/bin/env python3
"""Push Docker state/health only to fixed native Gatus external endpoints.

Run by a host timer. No container exec, logs, environment inspection, Docker
socket mount, system mutation, or provider credentials. Tokens never reach agents.
"""

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = (
    '{"name":{{json .Name}},"status":{{json .State.Status}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},'
    '"swarm_service":{{json (index .Config.Labels "com.docker.swarm.service.name")}}}'
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Monitoring redirects forbidden")


def evaluate(check, containers):
    matches = [
        c
        for c in containers
        if (
            c["name"].lstrip("/") == check.get("container")
            or check.get("swarm_service")
            and c.get("swarm_service") == check["swarm_service"]
        )
    ]
    if len(matches) != 1:
        return False, "expected container missing or ambiguous"
    c = matches[0]
    return c["status"] == "running" and c["health"] in ("healthy", "none"), "state=" + c["status"] + "; health=" + c[
        "health"
    ]


def collect():
    ids = subprocess.check_output(["docker", "ps", "-aq"], text=True, timeout=15).split()
    if not ids or len(ids) > 300 or any(not re.fullmatch("[a-f0-9]{12,64}", i) for i in ids):
        raise RuntimeError("Docker inventory unavailable")
    raw = subprocess.check_output(["docker", "inspect", "--format", FORMAT, *ids], text=True, timeout=20)
    return [json.loads(line) for line in raw.splitlines()]


def run(config_path, credentials_path):
    cfg = json.loads(Path(config_path).read_text())
    secret = json.loads(Path(credentials_path).read_text())
    assert cfg["url"] == "https://gatus.kodeme.io"
    assert len(secret["token"]) >= 32
    containers = collect()
    failures = 0
    opener = urllib.request.build_opener(NoRedirect)
    for check in cfg["checks"]:
        key = check["key"]
        assert re.fullmatch("[a-z0-9-]+_[a-z0-9-]+", key)
        success, reason = evaluate(check, containers)
        query = urllib.parse.urlencode({"success": str(success).lower(), "error": reason})
        request = urllib.request.Request(
            cfg["url"] + "/api/v1/endpoints/" + key + "/external?" + query,
            data=b"",
            method="POST",
            headers={"Authorization": "Bearer " + secret["token"]},
        )
        try:
            with opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise RuntimeError("Push refused")
        except Exception:
            failures += 1
    print(json.dumps({"checks": len(cfg["checks"]), "push_failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(*sys.argv[1:]))
