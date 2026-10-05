"""Operator SSH transport for monitoring only, behind the Dokploy front door.

Resolve the server through the selected Dokploy API; never accept an arbitrary
SSH destination or shell command. Scripts enter Python through stdin. Preview
writes unless --apply is supplied. No profiles or host credentials enter OMP.
"""

import argparse
import json
import re
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("server_id")
    parser.add_argument(
        "action", choices=("facts", "status", "install-config", "load-image", "glitchtip-python", "collector-python")
    )
    parser.add_argument("--container")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = subprocess.run(
        ["kctl-dokploy", "-p", args.profile, "--json", "servers", "get", args.server_id],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    server = json.loads(result.stdout)
    ip, user = server["ipAddress"], server.get("username") or "root"
    if not re.fullmatch(r"[a-zA-Z0-9.:_-]+", ip) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("invalid server address")
    if args.action not in {"facts", "status"} and not args.apply:
        print(
            json.dumps(
                {"server_id": args.server_id, "action": args.action, "container": args.container, "applied": False}
            )
        )
        return
    if args.action == "facts":
        remote = "docker compose version --short && docker network ls --format '{{.Name}}'"
    elif args.action == "status":
        remote = (
            "docker ps -a --filter name=tpp-infra- --format '{{.Names}} {{.Status}} {{.Image}}'; "
            "tail -n 30 /etc/dokploy/logs/compose-program-virtual-feed-4yld8w/*.log; "
            "docker logs --tail 15 tpp-infra-incident-ingress; "
            "docker logs --tail 15 tpp-infra-incident-collector; "
            "docker logs --tail 15 tpp-infra-gatus"
        )
    elif args.action == "load-image":
        remote = "docker load"
    elif args.action == "install-config":
        code = """
import json, os, pathlib, tempfile, sys
files = json.load(sys.stdin)
assert set(files) == {'gatus.json', 'services.json'}
root = pathlib.Path('/etc/dokploy/monitoring/tpp-infra-gatus')
root.mkdir(parents=True, exist_ok=True)
for name, content in files.items():
    assert isinstance(content, str) and len(content) <= 65536
    json.loads(content)
    fd, tmp = tempfile.mkstemp(dir=root)
    with os.fdopen(fd, 'w') as out:
        out.write(content)
        os.fchmod(out.fileno(), 0o644)
    os.replace(tmp, root / name)
print('Installed two monitoring configuration files')
"""
        remote = shlex.join(["python3", "-c", code])
    else:
        name = args.container or ""
        if args.action == "glitchtip-python":
            allowed = name == "compose-calculate-bluetooth-card-38zbkf"
            command = [
                "docker",
                "exec",
                "-i",
                name,
                "python",
                "manage.py",
                "shell",
                "-c",
                "import sys; exec(sys.stdin.read())",
            ]
        else:
            allowed = bool(re.fullmatch(r"tpp-infra-incident-(ingress|collector)", name))
            command = ["docker", "exec", "-i", name, "python", "-c", "import sys; exec(sys.stdin.read())"]
        if not allowed:
            raise ValueError("container is outside the monitoring allowlist")
        remote = shlex.join(command)
    raise SystemExit(
        subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=8",
                "-p",
                str(server.get("port") or 22),
                f"{user}@{ip}",
                remote,
            ],
            stdin=None if args.action not in {"facts", "status"} else subprocess.DEVNULL,
        ).returncode
    )


if __name__ == "__main__":
    main()
