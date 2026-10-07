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
        "action",
        choices=(
            "facts",
            "status",
            "install-config",
            "load-image",
            "glitchtip-python",
            "collector-python",
            "protect-sso-file",
            "host-health-inventory",
            "install-host-health",
            "host-health-status",
        ),
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
    if args.action not in {"facts", "status", "host-health-inventory", "host-health-status"} and not args.apply:
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
    elif args.action == "host-health-inventory":
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "monitoring/idtpp/host_health.py"
        spec = spec_from_file_location("host_health", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        code = (
            "import subprocess,json\nFORMAT="
            + repr(module.FORMAT)
            + "\n"
            + """
ids=subprocess.check_output(['docker','ps','-aq'],text=True,timeout=8).split()
rows=subprocess.check_output(['docker','inspect','--format',FORMAT,*ids],text=True,timeout=10) if ids else ''
print(json.dumps([json.loads(x) for x in rows.splitlines()]))
"""
        )
        remote = shlex.join(["python3", "-c", code])
    elif args.action == "host-health-status":
        remote = (
            "systemctl is-active kodemeio-gatus-health.timer && "
            "journalctl -u kodemeio-gatus-health.service -n 3 --no-pager -o cat"
        )
    elif args.action == "install-host-health":
        import sys
        from pathlib import Path

        payload = json.load(sys.stdin)
        source = Path(__file__).resolve().parents[1] / "monitoring/idtpp/host_health.py"
        if payload.get("script") != source.read_text():
            raise ValueError("exporter script must match reviewed repository source")
        code = r"""
import json,os,pathlib,re,subprocess,sys
payload=json.load(sys.stdin)
assert set(payload)=={'config','script'}
config=payload['config']
assert set(config)=={'url','token','targets'}
assert config['url']=='https://gatus.idtpp.com'
assert re.fullmatch('[A-Za-z0-9_-]{32,128}',config['token'])
assert 1<=len(config['targets'])<=30
for t in config['targets']:
 assert set(t)=={'key','project','services','init_services'}
 assert re.fullmatch('tpp-runtime_tpp[a-z0-9_-]*',t['key'])
 assert re.fullmatch('[a-z0-9_-]{1,100}',t['project'])
 assert t['services'] and all(re.fullmatch('[A-Za-z0-9_-]{1,100}',v) for v in t['services']+t['init_services'])
root=pathlib.Path('/etc/kodemeio/gatus-exporter');root.mkdir(parents=True,exist_ok=True,mode=0o700)
script=pathlib.Path('/usr/local/lib/kodemeio/gatus_host_health.py');script.parent.mkdir(parents=True,exist_ok=True)
(root/'config.json').write_text(json.dumps(config));os.chmod(root/'config.json',0o600)
script.write_text(payload['script']);os.chmod(script,0o644)
pathlib.Path('/etc/systemd/system/kodemeio-gatus-health.service').write_text(
 '[Unit]\n'
 'Description=TPP container readiness to Gatus\n'
 'After=docker.service network-online.target\n'
 '[Service]\n'
 'Type=oneshot\n'
 'ExecStart=/usr/bin/python3 /usr/local/lib/kodemeio/gatus_host_health.py\n'
 'TimeoutStartSec=40\n'
 'NoNewPrivileges=true\n'
 'ProtectSystem=strict\n'
 'ProtectHome=true\n'
 'PrivateTmp=true\n'
 'ReadOnlyPaths=/etc/kodemeio/gatus-exporter\n'
)
pathlib.Path('/etc/systemd/system/kodemeio-gatus-health.timer').write_text(
 '[Unit]\n'
 'Description=TPP Gatus readiness heartbeat\n'
 '[Timer]\n'
 'OnBootSec=30s\n'
 'OnUnitActiveSec=60s\n'
 'AccuracySec=5s\n'
 '[Install]\n'
 'WantedBy=timers.target\n'
)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','kodemeio-gatus-health.timer'],check=True)
subprocess.run(['systemctl','start','kodemeio-gatus-health.service'],check=True)
print('Installed fixed Gatus readiness heartbeat')
"""
        remote = shlex.join(["python3", "-c", code])
    elif args.action == "load-image":
        remote = "docker load"
    elif args.action == "protect-sso-file":
        remote = (
            "chmod 600 /etc/dokploy/traefik/dynamic/tpp-gatus-authentik.yml && "
            "stat -c '%a' /etc/dokploy/traefik/dynamic/tpp-gatus-authentik.yml"
        )
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
            input=json.dumps(payload).encode() if args.action == "install-host-health" else None,
            stdin=None
            if args.action not in {"facts", "status", "host-health-inventory", "host-health-status"}
            else subprocess.DEVNULL,
        ).returncode
    )


if __name__ == "__main__":
    main()
