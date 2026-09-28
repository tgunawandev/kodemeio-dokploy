#!/usr/bin/env python3
"""Roadmap row 0.3 -- kod Postgres ports unpublished, delete protection on.

Gate: external probe fails; API shows protection.

Subcommands:
  ports       Static. For every kod-*/tkz-* manifest whose source repo is
              kodemeio-postgres, renders that repo's compose `ports:` with the
              manifest's env_defaults + env_overrides (compose interpolation
              rules) and fails on any publish of 5432/6432/9187 whose host IP is
              not loopback. Both the manifest's compose_path (kctl-dokploy
              default docker-compose.yml) and docker-compose.prod.yml are
              checked, since the live compose path is a founder confirmation.
              The gitignored env_file is never read -- only committed
              env_overrides can make a manifest pass. Also scans this repo's
              own *compose*.y*ml files for the same ports.
  protection  A `kctl-hz -p <profile> --json servers list` export per Hetzner
              profile against ops/wave0/s0/protection.kod.yaml: every listed
              server must exist with protection.delete AND protection.rebuild
              true (Hetzner requires both to match).
  probe       TCP connect to HOST:PORT... from OUTSIDE the host. Any open port
              fails. Refuses loopback/private targets unless --allow-local.

Exit: 0 pass, 1 finding, 2 input error.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import yaml

SENSITIVE_PORTS = (5432, 6432, 9187)
KOD_PREFIXES = ("kod-", "tkz-")
POSTGRES_REPO = "kodemeio-postgres"
DEFAULT_COMPOSE = "docker-compose.yml"  # kctl-dokploy SourceConfig default
EXTRA_COMPOSE = ("docker-compose.prod.yml",)
# `${VAR:+alt}` is not supported: it stays literal, fails port parsing and is
# reported as an "unresolved" finding -- closed, never a silent pass.
_VAR_RE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class InputError(ValueError):
    """The inputs cannot support a verdict."""


# --- compose helpers ----------------------------------------------------------


def interpolate(value: str, env: dict[str, str]) -> str:
    """Compose-style `$$`, `$VAR`, `${VAR}`, `${VAR:-d}`, `${VAR-d}`, `${VAR:?e}`, `${VAR?e}`.

    Stricter than compose in one place: an unset `${VAR}` / `$VAR` with no
    default is an error (compose would substitute "" with a warning).
    """

    def sub(m: re.Match) -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group(1) or m.group(4)
        op, arg = m.group(2), m.group(3) or ""
        present, val = name in env, env.get(name, "")
        if op == ":-":
            return val if val else arg
        if op == "-":
            return val if present else arg
        if op == ":?" and val:
            return val
        if op == "?" and present:
            return val
        if op is None and present:
            return val
        raise InputError(f"variable {name} is unset and has no default")

    return _VAR_RE.sub(sub, value)


def _range(text: str) -> list[int]:
    text = text.strip()
    if "-" in text:
        lo, hi = text.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(text)]


def parse_port(spec) -> list[tuple[str, int]]:
    """[(host_ip, container_port)] for one compose `ports:` entry."""
    try:
        if isinstance(spec, dict):  # long syntax; no `published` still publishes an ephemeral port
            return [(str(spec.get("host_ip") or "0.0.0.0"), int(spec["target"]))]
        text = str(spec).split("/", 1)[0].strip()
        host_ip = "0.0.0.0"
        if text.startswith("["):
            host_ip, _, rest = text[1:].partition("]:")
            parts = rest.split(":")
        else:
            parts = text.split(":")
            if len(parts) == 3:
                host_ip = parts[0] or "0.0.0.0"
                parts = parts[1:]
            elif len(parts) > 3:
                raise ValueError(spec)
        container = parts[-1]
        return [(host_ip, p) for p in _range(container)]
    except (KeyError, TypeError, ValueError) as exc:
        raise InputError(f"unparseable ports entry {spec!r}") from exc


def _is_loopback(host_ip: str) -> bool:
    try:
        return ipaddress.ip_address(host_ip).is_loopback
    except ValueError:
        return False


def _compose_findings(compose: Path, env: dict[str, str], label: str) -> list[dict]:
    try:
        doc = yaml.safe_load(compose.read_text())
    except OSError as exc:
        raise InputError(f"cannot read compose file {compose}") from exc
    except yaml.YAMLError as exc:
        raise InputError(f"compose file {compose} is not valid YAML") from exc
    findings = []
    services = (doc or {}).get("services") or {}
    if not isinstance(services, dict):
        return findings
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        for entry in svc.get("ports") or []:
            try:
                rendered = (
                    {k: interpolate(str(v), env) if isinstance(v, str) else v for k, v in entry.items()}
                    if isinstance(entry, dict)
                    else interpolate(str(entry), env)
                )
                bindings = parse_port(rendered)
            except InputError as exc:
                findings.append(
                    {
                        "compose": label,
                        "service": svc_name,
                        "container_port": None,
                        "host_ip": "unresolved",
                        "detail": str(exc),
                    }
                )
                continue
            for host_ip, port in bindings:
                if port in SENSITIVE_PORTS and not _is_loopback(host_ip):
                    findings.append({"compose": label, "service": svc_name, "container_port": port, "host_ip": host_ip})
    return findings


# --- ports: manifests -----------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"cannot load {path}") from exc
    return data if isinstance(data, dict) else {}


def _manifest_env_and_source(path: Path) -> tuple[dict[str, str], dict]:
    body = _load_yaml(path)
    base: dict = {}
    if body.get("extends"):
        base = _load_yaml((path.parent / body["extends"]).resolve())
    env: dict[str, str] = {}
    for layer in (
        base.get("env_defaults"),
        body.get("env_defaults"),
        base.get("env_overrides"),
        body.get("env_overrides"),
    ):
        env.update({str(k): str(v) for k, v in (layer or {}).items()})
    source = {**(base.get("source") or {}), **(body.get("source") or {}), **(body.get("source_overrides") or {})}
    return env, source


def check_ports(manifests_dir: Path, postgres_repo: Path) -> list[dict]:
    manifests_dir, postgres_repo = Path(manifests_dir), Path(postgres_repo)
    if not manifests_dir.is_dir():
        raise InputError(f"manifests dir not found: {manifests_dir}")
    findings: list[dict] = []
    for path in sorted(manifests_dir.glob("*.yaml")):
        if not path.name.startswith(KOD_PREFIXES):
            continue
        env, source = _manifest_env_and_source(path)
        if source.get("repo") != POSTGRES_REPO:
            continue
        candidates = [str(source.get("compose_path") or DEFAULT_COMPOSE).removeprefix("./"), *EXTRA_COMPOSE]
        for rel in dict.fromkeys(candidates):
            compose = postgres_repo / rel
            if not compose.is_file():
                raise InputError(f"{path.name}: compose file {rel} not found in {postgres_repo}")
            for f in _compose_findings(compose, env, rel):
                findings.append({"manifest": path.name, **f})
    return findings


def repo_compose_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "*compose*.yml", "*compose*.yaml"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise InputError(f"git ls-files failed in {root}")
    return [root / line for line in out.stdout.splitlines() if line]


def check_compose_files(files: list[Path], base: Path) -> list[dict]:
    findings = []
    for f in files:
        findings.extend(_compose_findings(f, {}, str(Path(f).relative_to(base))))
    return findings


# --- protection -----------------------------------------------------------------


def validate_protection(want: dict) -> None:
    if not isinstance(want, dict) or want.get("version") != 1:
        raise InputError("protection desired state: expected version: 1")
    if want.get("require") != {"delete": True, "rebuild": True}:
        raise InputError("protection desired state: require must be {delete: true, rebuild: true}")
    profiles = want.get("hetzner_profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise InputError("protection desired state: hetzner_profiles must be a non-empty mapping")
    for profile, names in profiles.items():
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
            raise InputError(f"protection desired state: profile {profile} needs a non-empty list of server names")


def check_protection(want: dict, exports: dict[str, list]) -> dict:
    validate_protection(want)
    failures, unlisted = [], []
    for profile, required in want["hetzner_profiles"].items():
        if profile not in exports:
            raise InputError(f"no servers export supplied for Hetzner profile {profile}")
        servers = exports[profile]
        if not isinstance(servers, list) or not all(isinstance(s, dict) for s in servers):
            raise InputError(f"export for {profile} must be a JSON list of server objects")
        by_name: dict[str, list[dict]] = {}
        for s in servers:
            by_name.setdefault(str(s.get("name")), []).append(s)
        for name in required:
            found = by_name.get(name, [])
            if len(found) != 1:
                problem = "not found in export" if not found else "name is ambiguous in export"
                failures.append({"profile": profile, "server": name, "problem": problem})
                continue
            protection = found[0].get("protection")
            protection = protection if isinstance(protection, dict) else {}
            for key in ("delete", "rebuild"):
                if protection.get(key) is not True:
                    failures.append({"profile": profile, "server": name, "problem": f"protection.{key} is not true"})
        unlisted.extend(f"{profile}:{n}" for n in sorted(by_name) if n not in required)
    return {"status": "unprotected" if failures else "protected", "failures": failures, "unlisted": unlisted}


# --- probe --------------------------------------------------------------------------


def probe(host: str, ports: list[int], timeout: float = 3.0, allow_local: bool = False) -> dict:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise InputError(f"cannot resolve {host}") from exc
    addrs = sorted({info[4][0] for info in infos})
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local) and not allow_local:
            raise InputError(f"{host} resolves to {a}: run the probe from outside the host, against its public address")
    results: dict[str, str] = {}
    for port in ports:
        state = "closed"
        for a in addrs:
            family = socket.AF_INET6 if ":" in a else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                try:
                    if s.connect_ex((a, port)) == 0:
                        state = "open"
                except OSError:
                    pass
        results[str(port)] = state
    exposed = any(v == "open" for v in results.values())
    return {"host": host, "addresses": addrs, "ports": results, "status": "exposed" if exposed else "not-exposed"}


# --- CLI ------------------------------------------------------------------------------


def _read_json(path: str):
    try:
        return json.loads(Path(path).read_text())
    except OSError as exc:
        raise InputError(f"cannot read {path}") from exc
    except json.JSONDecodeError:
        raise InputError(f"{path} is not valid JSON") from None


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ports")
    p.add_argument("--manifests", default=str(repo_root / "deploys/instances/production"))
    p.add_argument("--postgres-repo", default=str(repo_root.parent / POSTGRES_REPO))
    p.add_argument("--no-repo-scan", action="store_true", help="skip this repo's own compose files")
    pr = sub.add_parser("protection")
    pr.add_argument("--desired", default=str(repo_root / "ops/wave0/s0/protection.kod.yaml"))
    pr.add_argument("--export", action="append", default=[], metavar="PROFILE=FILE", required=True)
    pb = sub.add_parser("probe")
    pb.add_argument("host")
    pb.add_argument("--ports", default=",".join(str(p) for p in SENSITIVE_PORTS))
    pb.add_argument("--timeout", type=float, default=3.0)
    pb.add_argument("--allow-local", action="store_true", help="tests only: permit a loopback/private target")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "ports":
            findings = check_ports(Path(args.manifests), Path(args.postgres_repo))
            if not args.no_repo_scan:
                findings += check_compose_files(repo_compose_files(repo_root), base=repo_root)
            if findings:
                print(f"FAIL: {len(findings)} public Postgres port publish(es):")
                for f in findings:
                    where = f.get("manifest", "repo")
                    print(
                        f"  {where} {f['compose']} service={f['service']}"
                        f" port={f['container_port']} host_ip={f['host_ip']}"
                    )
                return 1
            print("PASS: no kod Postgres port (5432/6432/9187) is published beyond loopback")
            return 0
        if args.cmd == "protection":
            exports = {}
            for item in args.export:
                profile, sep, path = item.partition("=")
                if not sep:
                    raise InputError("--export must be PROFILE=FILE")
                exports[profile] = _read_json(path)
            report = check_protection(_load_yaml(Path(args.desired)), exports)
            print(f"S0 row 0.3 delete protection: {report['status'].upper()}")
            for f in report["failures"]:
                print(f"  FAIL {f['profile']}:{f['server']}: {f['problem']}")
            for u in report["unlisted"]:
                print(f"  note (not in desired state, not gated): {u}")
            return 0 if report["status"] == "protected" else 1
        try:
            ports = [int(x) for x in args.ports.split(",") if x.strip()]
        except ValueError:
            raise InputError("--ports must be a comma-separated list of integers") from None
        report = probe(args.host, ports, args.timeout, args.allow_local)
        print(f"S0 row 0.3 external probe {report['host']}: {report['status'].upper()}")
        for port, state in report["ports"].items():
            print(f"  {port}: {state}")
        return 1 if report["status"] == "exposed" else 0
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
