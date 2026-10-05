"""Read-only SSH inventory. Select fields before emitting any API result."""

import argparse
import concurrent.futures
import json
import re
import subprocess
from datetime import UTC, datetime


def probe(server, diagnose=False):
    row = {key: server.get(key) for key in ("name", "serverId", "ipAddress", "serverStatus")}
    ip = server.get("ipAddress", "")
    user = server.get("username") or "root"
    if not re.fullmatch(r"[a-zA-Z0-9.:_-]+", ip) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        return {**row, "probe": "invalid_target"}
    result = subprocess.run(
        [
            "ssh",
            "-n",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=yes",
            "-p",
            str(server.get("port") or 22),
            f"{user}@{ip}",
            "docker ps -a --format '{{json .}}'",
        ],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if result.returncode:
        return {**row, "probe": "unreachable"}
    containers = []
    for line in result.stdout.splitlines():
        item = json.loads(line)
        containers.append({key: item.get(key) for key in ("Names", "Image", "State", "Status")})
        if diagnose and item.get("Names") in {"kod-infra-hermes-vision-init", "kod-dsh-outpost-authentik-outpost-1"}:
            logs = subprocess.run(
                [
                    "ssh",
                    "-n",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=8",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-p",
                    str(server.get("port") or 22),
                    f"{user}@{ip}",
                    "docker logs --tail 100 " + item["Names"],
                ],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            selected = []
            for line in (logs.stdout + logs.stderr).splitlines():
                if not re.search(r"(?i)error|exception|fail|required|invalid|timed out|unreachable", line):
                    continue
                line = re.sub(r"https?://[^\s<>]+", "[url]", line)
                line = re.sub(
                    r"(?i)(?:password|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*[^\s,;]+",
                    "[credential]",
                    line,
                )
                line = re.sub(r"(?i)\b(?:bearer|basic)\s+[\w.+/=-]+", "[credential]", line)
                line = re.sub(r"[A-Za-z0-9_+/=-]{32,}", "[long-value]", line)
                line = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", line)
                selected.append(line[:300])
            containers[-1]["diagnostic_lines"] = selected[-8:]
    return {**row, "probe": "ok", "containers": containers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="bounded redacted errors from VISION init and DSH outpost only; do not commit logs",
    )
    args = parser.parse_args()
    result = subprocess.run(
        ["kctl-dokploy", "-p", args.profile, "--json", "servers", "list"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    servers = json.loads(result.stdout)
    if isinstance(servers, dict):
        servers = servers.get("data", [])
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(probe, server, args.diagnose): server for server in servers}
        for job in concurrent.futures.as_completed(jobs):
            try:
                rows.append(job.result())
            except (ValueError, OSError, subprocess.TimeoutExpired):
                rows.append({"name": jobs[job].get("name"), "probe": "failed"})
    print(
        json.dumps(
            {"observed_at": datetime.now(UTC).isoformat(), "hosts": sorted(rows, key=lambda r: r["name"])}, indent=2
        )
    )
    return int(any(row["probe"] != "ok" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
