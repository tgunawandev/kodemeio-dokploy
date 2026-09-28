#!/usr/bin/env python3
"""Roadmap row 0.1 -- dangling-DNS checker for the kod zones.

Offline. Reads, never fetches:
  * the desired state (ops/wave0/s0/dns.kod.yaml),
  * one Cloudflare zone export per zone, as `kctl-cf -p kodemeio --json
    records list --zone <zone>` prints it (a JSON list of record objects),
  * one or more Hetzner exports as the OWNED IP inventory, as `kctl-hz -p
    <profile> --json servers list` prints it (server objects with
    `public_net`), or a primary/floating IP list (objects with `ip`).

Gate (roadmap 0.1): no record resolves outside our Hetzner projects.

Classes:
  dangling        A/AAAA whose address is not owned and not allow-listed
  orphaned        CNAME/MX/SRV/NS whose in-zone target does not exist (or is
                  itself being removed -- chains are followed to a fixpoint)
  unowned-target  CNAME/MX/SRV/NS target outside the checked zones and not on
                  an allow-listed suffix
  retired         any record on a retired hostname

Prints record id/type/name/content only. Exit: 0 clean, 1 findings, 2 input
error. The removal plan is text for the founder; this script runs nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import re
import sys
from pathlib import Path

import yaml

RECORD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ZONE_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
ADDRESS_TYPES = {"A", "AAAA"}
TARGET_TYPES = {"CNAME", "MX", "SRV", "NS"}
DEPENDENT_TYPES = {"CNAME"}  # removal order: a CNAME goes before what it points at


class InputError(ValueError):
    """The inputs cannot support a verdict; never reported as clean."""


def norm(name: str) -> str:
    return str(name).strip().rstrip(".").lower()


# --- desired state ------------------------------------------------------------


def validate_desired(want: dict) -> None:
    if not isinstance(want, dict) or want.get("version") != 1:
        raise InputError("desired state: expected a mapping with version: 1")
    profile = want.get("cloudflare_profile")
    if not isinstance(profile, str) or not PROFILE_RE.match(profile):
        raise InputError("desired state: cloudflare_profile must be a plain profile name")
    zones = want.get("zones")
    if not isinstance(zones, list) or not zones:
        raise InputError("desired state: zones must be a non-empty list")
    for zone in zones:
        if not isinstance(zone, str) or not ZONE_RE.match(zone):
            raise InputError(f"desired state: invalid zone name {zone!r}")
    for key, field in (("allowed_external_targets", "suffix"), ("allowed_ips", "ip"), ("retired_hostnames", "name")):
        entries = want.get(key, [])
        if not isinstance(entries, list):
            raise InputError(f"desired state: {key} must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get(field), str) or not entry[field].strip():
                raise InputError(f"desired state: every {key} entry needs a {field}")
            if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
                raise InputError(f"desired state: {key} entry {entry.get(field)!r} needs a reason")
            if key == "allowed_ips":
                try:
                    ipaddress.ip_address(entry["ip"])
                except ValueError as exc:
                    raise InputError(f"desired state: allowed_ips entry {entry['ip']!r} is not an IP") from exc


# --- owned inventory ----------------------------------------------------------


def owned_inventory(exports: list) -> tuple[set, list]:
    """(set of owned IPv4/IPv6 addresses, list of owned IPv6 networks)."""
    addrs: set = set()
    nets: list = []

    def add(value) -> None:
        if not isinstance(value, str) or not value:
            return
        if "/" in value:
            net = ipaddress.ip_network(value, strict=False)
            (nets.append(net) if net.num_addresses > 1 else addrs.add(net.network_address))
        else:
            addrs.add(ipaddress.ip_address(value))

    for export in exports:
        if not isinstance(export, list):
            raise InputError("hetzner export: expected a JSON list")
        for obj in export:
            if not isinstance(obj, dict):
                raise InputError("hetzner export: expected objects")
            try:
                public_net = obj.get("public_net")
                if isinstance(public_net, dict):
                    for family in ("ipv4", "ipv6"):
                        block = public_net.get(family)
                        if isinstance(block, dict):
                            add(block.get("ip"))
                    for fip in public_net.get("floating_ips") or []:
                        if isinstance(fip, str):
                            add(fip)
                elif "ip" in obj:
                    add(obj.get("ip"))
                else:
                    raise InputError("hetzner export: object has neither public_net nor ip")
            except ValueError as exc:
                if isinstance(exc, InputError):
                    raise
                raise InputError("hetzner export: unparseable IP value") from exc
    if not addrs and not nets:
        raise InputError("hetzner export: no owned IP found -- refusing to call every record dangling")
    return addrs, nets


# --- records ------------------------------------------------------------------


def load_records(zone: str, records) -> list[dict]:
    if not isinstance(records, list):
        raise InputError(f"zone {zone}: export must be a JSON list")
    out = []
    for r in records:
        if not isinstance(r, dict):
            raise InputError(f"zone {zone}: every record must be an object")
        rid = r.get("id")
        if not isinstance(rid, str) or not RECORD_ID_RE.match(rid):
            raise InputError(f"zone {zone}: record id is not a 32-hex Cloudflare id")
        rtype = str(r.get("type", "")).upper()
        name = norm(r.get("name", ""))
        if not rtype or not name:
            raise InputError(f"zone {zone}: record {rid} lacks type or name")
        if name != zone and not name.endswith("." + zone):
            raise InputError(f"zone {zone}: record {rid} name is outside zone")
        content = str(r.get("content", "")).strip()
        out.append({"zone": zone, "id": rid, "type": rtype, "name": name, "content": content})
    return out


def target_of(record: dict) -> str:
    content = record["content"]
    if record["type"] in {"SRV", "MX"}:  # "prio weight port target" / "prio target"
        parts = content.split()
        content = parts[-1] if parts else ""
    return norm(content)


def in_zones(name: str, zones: list[str]) -> bool:
    return any(name == z or name.endswith("." + z) for z in zones)


def suffix_allowed(name: str, suffixes: list[str]) -> bool:
    return any(name == s or name.endswith("." + s) for s in suffixes)


def name_exists(name: str, live: dict[str, set]) -> bool:
    """True if `name` has a record in `live` (exact or via a wildcard)."""
    if name in live and live[name]:
        return True
    labels = name.split(".")
    for i in range(1, len(labels) - 1):
        wildcard = "*." + ".".join(labels[i:])
        if wildcard in live and live[wildcard]:
            return True
    return False


def address_owned(content: str, addrs: set, nets: list, allowed: set) -> bool:
    try:
        ip = ipaddress.ip_address(content)
    except ValueError:
        return False
    return ip in addrs or ip in allowed or any(ip in n for n in nets)


# --- check --------------------------------------------------------------------


def check(want: dict, zone_records: dict, inventories: list) -> dict:
    validate_desired(want)
    zones = [norm(z) for z in want["zones"]]
    for zone in zones:
        if zone not in zone_records:
            raise InputError(f"no export supplied for zone {zone}")
    addrs, nets = owned_inventory(inventories)
    allowed_ips = {ipaddress.ip_address(e["ip"]) for e in want.get("allowed_ips", [])}
    suffixes = [norm(e["suffix"]) for e in want.get("allowed_external_targets", [])]
    retired = {norm(e["name"]): e["reason"] for e in want.get("retired_hostnames", [])}

    records = []
    for zone in zones:
        records.extend(load_records(zone, zone_records[zone]))

    findings: dict[str, dict] = {}

    def flag(r: dict, cls: str, reason: str) -> None:
        findings.setdefault(r["id"], {**r, "class": cls, "reason": reason})

    for r in records:
        if r["name"] in retired:
            flag(r, "retired", f"retired hostname: {retired[r['name']]}")
        elif r["type"] in ADDRESS_TYPES and not address_owned(r["content"], addrs, nets, allowed_ips):
            flag(r, "dangling", "address is not in any supplied Hetzner inventory or allowed_ips")
        elif r["type"] in TARGET_TYPES:
            target = target_of(r)
            if not target:
                flag(r, "orphaned", "record has no target")
            elif not in_zones(target, zones) and not suffix_allowed(target, suffixes):
                flag(r, "unowned-target", "target is outside the checked zones and not allow-listed")

    # Fixpoint: an in-zone target that is missing, or whose every record is
    # being removed, orphans the records pointing at it.
    changed = True
    while changed:
        changed = False
        live: dict[str, set] = {}
        for r in records:
            if r["id"] not in findings:
                live.setdefault(r["name"], set()).add(r["id"])
        for r in records:
            if r["id"] in findings or r["type"] not in TARGET_TYPES:
                continue
            target = target_of(r)
            if in_zones(target, zones) and not name_exists(target, live):
                flag(r, "orphaned", "in-zone target does not exist once dangling records are removed")
                changed = True

    ordered = sorted(findings.values(), key=lambda f: (f["zone"], f["name"], f["type"], f["id"]))
    return {
        "status": "findings" if ordered else "clean",
        "cloudflare_profile": want["cloudflare_profile"],
        "zones": {z: sum(1 for r in records if r["zone"] == z) for z in zones},
        "findings": ordered,
        "removal_plan": removal_plan(want["cloudflare_profile"], zones, ordered),
    }


def removal_plan(profile: str, zones: list[str], findings: list[dict]) -> list[str]:
    if not findings:
        return []
    stamp = dt.date.today().strftime("%Y%m%d")
    plan = [f"kctl-cf -p {profile} records export --zone {z} -f dns-backup-{z}-{stamp}.bind" for z in zones]
    # CNAMEs (dependents) first, so no step leaves a live alias to a removed name.
    order = sorted(findings, key=lambda f: (f["type"] not in DEPENDENT_TYPES, f["zone"], f["name"], f["id"]))
    for flag_ in ("--explain", "--force"):
        plan.extend(f"kctl-cf -p {profile} records delete {f['id']} --zone {f['zone']} {flag_}" for f in order)
    return plan


# --- CLI ----------------------------------------------------------------------


def _read_json(path: str):
    try:
        return json.loads(Path(path).read_text())
    except OSError as exc:
        raise InputError(f"cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON (line {exc.lineno})") from None


def render_text(report: dict) -> str:
    lines = [f"S0 row 0.1 DNS check: {'CLEAN' if report['status'] == 'clean' else 'FINDINGS'}"]
    for zone, count in report["zones"].items():
        lines.append(f"  zone {zone}: {count} records")
    for f in report["findings"]:
        lines.append(f"  [{f['class']}] {f['zone']} {f['type']} {f['name']} -> {f['content']} (id {f['id']})")
    if report["removal_plan"]:
        lines.append("Removal plan (founder runs; export first, --explain before --force):")
        lines.extend(f"  {step}" for step in report["removal_plan"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="classify zone records against the owned-IP inventory")
    c.add_argument("--desired", required=True)
    c.add_argument("--zone-export", action="append", default=[], metavar="ZONE=FILE", required=True)
    c.add_argument("--hetzner", action="append", default=[], metavar="FILE", required=True)
    c.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        try:
            want = yaml.safe_load(Path(args.desired).read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise InputError(f"cannot load desired state {args.desired}") from exc
        zone_records = {}
        for item in args.zone_export:
            zone, sep, path = item.partition("=")
            if not sep:
                raise InputError("--zone-export must be ZONE=FILE")
            zone_records[norm(zone)] = _read_json(path)
        report = check(want, zone_records, [_read_json(p) for p in args.hetzner])
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
