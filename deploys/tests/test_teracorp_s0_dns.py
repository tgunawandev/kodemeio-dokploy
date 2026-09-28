"""Roadmap row 0.1 -- the dangling-DNS checker for the kod zones.

Every fixture is synthetic: TEST-NET IPv4 (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24), the 2001:db8::/32 documentation prefix, and invented record
ids. Nothing here contacts Cloudflare or Hetzner.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ops/scripts/teracorp_s0_dns.py"
DESIRED = REPO_ROOT / "ops/wave0/s0/dns.kod.yaml"
SPEC = importlib.util.spec_from_file_location("teracorp_s0_dns", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
DNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DNS)

OWNED_V4 = "192.0.2.10"
OWNED_V4_B = "192.0.2.20"
FLOATING_V4 = "198.51.100.7"
RELEASED_V4 = "203.0.113.79"  # stands in for the released old kod-prod-02 IP
OWNED_V6_NET = "2001:db8:10::/64"


def rid(n: int) -> str:
    return f"{n:032x}"


def rec(n: int, rtype: str, name: str, content: str, proxied: bool = False) -> dict:
    return {"id": rid(n), "type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": 1}


def servers() -> list[dict]:
    return [
        {
            "id": 1,
            "name": "kod-prod-01",
            "status": "running",
            "public_net": {"ipv4": {"ip": OWNED_V4}, "ipv6": {"ip": OWNED_V6_NET}},
            "protection": {"delete": False, "rebuild": False},
        },
        {
            "id": 2,
            "name": "abc-prod-02",
            "status": "running",
            "public_net": {"ipv4": {"ip": OWNED_V4_B}, "ipv6": None},
            "protection": {"delete": False, "rebuild": False},
        },
    ]


def desired(**over) -> dict:
    base = {
        "version": 1,
        "estate": "kodeme.io",
        "cloudflare_profile": "kodemeio",
        "zones": ["example.com", "example.net"],
        "allowed_external_targets": [
            {"suffix": "mail.provider.example", "reason": "synthetic third-party MX"},
            {"suffix": "verify.saas.example", "reason": "synthetic domain-verification CNAME"},
        ],
        "allowed_ips": [],
        "retired_hostnames": [{"name": "old-gw.example.com", "reason": "service retired"}],
    }
    base.update(over)
    return base


def clean_zone_a() -> list[dict]:
    return [
        rec(1, "A", "example.com", OWNED_V4, proxied=True),
        rec(2, "A", "erp.example.com", OWNED_V4_B),
        rec(3, "AAAA", "v6.example.com", "2001:db8:10::5"),
        rec(4, "CNAME", "www.example.com", "example.com"),
        rec(5, "MX", "example.com", "mx1.mail.provider.example"),
        rec(6, "TXT", "example.com", "v=spf1 include:mail.provider.example -all"),
        rec(7, "CNAME", "_abc.example.com", "token.verify.saas.example"),
        rec(8, "CAA", "example.com", '0 issue "letsencrypt.org"'),
        rec(9, "A", "fip.example.com", FLOATING_V4),
    ]


def clean_zone_b() -> list[dict]:
    return [
        rec(20, "CNAME", "example.net", "erp.example.com"),
        rec(21, "A", "*.example.net", OWNED_V4),
        rec(22, "CNAME", "app.example.net", "x.example.net"),  # resolved by the wildcard
    ]


def floating() -> list[dict]:
    return [{"id": 9, "ip": FLOATING_V4, "type": "ipv4"}]


def run(zone_a, zone_b=None, want=None, inventories=None):
    want = want or desired()
    zones = {"example.com": zone_a, "example.net": zone_b if zone_b is not None else clean_zone_b()}
    inventories = inventories if inventories is not None else [servers(), floating()]
    return DNS.check(want, zones, inventories)


def classes(report) -> dict[str, str]:
    return {f["name"] + "/" + f["type"]: f["class"] for f in report["findings"]}


# --- classification -----------------------------------------------------------


def test_clean_zones_report_clean():
    report = run(clean_zone_a())
    assert report["status"] == "clean", report["findings"]
    assert report["findings"] == []
    assert report["removal_plan"] == []


def test_dangling_a_record_proxied_or_not():
    zone = clean_zone_a() + [
        rec(30, "A", "old.example.com", RELEASED_V4),
        rec(31, "A", "cdn.example.com", RELEASED_V4, proxied=True),
    ]
    report = run(zone)
    assert report["status"] == "findings"
    got = classes(report)
    assert got["old.example.com/A"] == "dangling"
    assert got["cdn.example.com/A"] == "dangling"  # proxy hides the origin, not the risk


def test_aaaa_outside_owned_64_is_dangling_inside_is_not():
    zone = clean_zone_a() + [rec(32, "AAAA", "gone6.example.com", "2001:db8:99::1")]
    got = classes(run(zone))
    assert got == {"gone6.example.com/AAAA": "dangling"}


def test_cname_to_missing_in_zone_name_is_orphaned():
    zone = clean_zone_a() + [rec(33, "CNAME", "shop.example.com", "nothere.example.com")]
    got = classes(run(zone))
    assert got == {"shop.example.com/CNAME": "orphaned"}


def test_cname_chain_through_dangling_record_is_orphaned_too():
    zone = clean_zone_a() + [
        rec(34, "A", "api.example.com", RELEASED_V4),
        rec(35, "CNAME", "api2.example.com", "api.example.com"),
        rec(36, "CNAME", "api3.example.com", "api2.example.com"),
    ]
    got = classes(run(zone))
    assert got == {
        "api.example.com/A": "dangling",
        "api2.example.com/CNAME": "orphaned",
        "api3.example.com/CNAME": "orphaned",
    }


def test_cross_zone_cname_resolves_against_other_checked_zone():
    zone_b = clean_zone_b() + [rec(40, "CNAME", "portal.example.net", "missing.example.com")]
    got = classes(run(clean_zone_a(), zone_b))
    assert got == {"portal.example.net/CNAME": "orphaned"}


def test_external_target_not_allowlisted_is_unowned():
    zone = clean_zone_a() + [
        rec(41, "CNAME", "blog.example.com", "someone.hosting.example"),
        rec(42, "MX", "example.com", "mx.unknown-mail.example"),
    ]
    got = classes(run(zone))
    assert got == {"blog.example.com/CNAME": "unowned-target", "example.com/MX": "unowned-target"}


def test_allowlist_suffix_matches_on_label_boundary_only():
    zone = clean_zone_a() + [rec(43, "CNAME", "x.example.com", "evilverify.saas.example")]
    got = classes(run(zone))
    assert got == {"x.example.com/CNAME": "unowned-target"}


def test_allowed_ip_with_reason_is_not_dangling():
    want = desired(allowed_ips=[{"ip": "198.51.100.200", "reason": "synthetic external host"}])
    zone = clean_zone_a() + [rec(44, "A", "ext.example.com", "198.51.100.200")]
    assert run(zone, want=want)["status"] == "clean"


def test_retired_hostname_is_listed_even_if_it_points_at_an_owned_ip():
    zone = clean_zone_a() + [rec(45, "A", "old-gw.example.com", OWNED_V4)]
    got = classes(run(zone))
    assert got == {"old-gw.example.com/A": "retired"}


def test_txt_and_caa_never_flagged():
    zone = clean_zone_a() + [rec(46, "TXT", "gone.example.com", "anything at all")]
    assert run(zone)["status"] == "clean"


def test_srv_target_is_checked():
    zone = clean_zone_a() + [rec(47, "SRV", "_sip._tcp.example.com", "10 5 5060 sip.gone.example")]
    assert classes(run(zone)) == {"_sip._tcp.example.com/SRV": "unowned-target"}


def test_trailing_dots_and_case_are_normalised():
    zone = clean_zone_a() + [rec(48, "CNAME", "WWW2.Example.com.", "ERP.example.COM.")]
    assert run(zone)["status"] == "clean"


# --- removal plan ---------------------------------------------------------------


def test_removal_plan_exports_first_then_explain_then_force():
    zone = clean_zone_a() + [rec(50, "A", "old.example.com", RELEASED_V4)]
    plan = run(zone)["removal_plan"]
    assert plan[0].startswith("kctl-cf -p kodemeio records export --zone example.com -f ")
    assert plan[1].startswith("kctl-cf -p kodemeio records export --zone example.net -f ")
    explain = [p for p in plan if p.endswith("--explain")]
    force = [p for p in plan if p.endswith("--force")]
    assert explain == [f"kctl-cf -p kodemeio records delete {rid(50)} --zone example.com --explain"]
    assert force == [f"kctl-cf -p kodemeio records delete {rid(50)} --zone example.com --force"]
    assert plan.index(explain[0]) < plan.index(force[0])


def test_removal_plan_deletes_dependents_before_their_targets():
    zone = clean_zone_a() + [
        rec(51, "A", "api.example.com", RELEASED_V4),
        rec(52, "CNAME", "api2.example.com", "api.example.com"),
    ]
    force = [p for p in run(zone)["removal_plan"] if p.endswith("--force")]
    assert force.index(f"kctl-cf -p kodemeio records delete {rid(52)} --zone example.com --force") < force.index(
        f"kctl-cf -p kodemeio records delete {rid(51)} --zone example.com --force"
    )


# --- fail closed on bad input --------------------------------------------------------


def test_missing_zone_export_is_an_input_error():
    with pytest.raises(DNS.InputError, match="example.net"):
        DNS.check(desired(), {"example.com": clean_zone_a()}, [servers()])


def test_empty_hetzner_inventory_is_an_input_error():
    with pytest.raises(DNS.InputError, match="owned IP"):
        DNS.check(desired(), {"example.com": clean_zone_a(), "example.net": clean_zone_b()}, [[]])


def test_record_outside_its_zone_is_an_input_error():
    zone = clean_zone_a() + [rec(60, "A", "foo.other.example", OWNED_V4)]
    with pytest.raises(DNS.InputError, match="outside zone"):
        run(zone)


def test_non_hex_record_id_is_refused_before_it_can_reach_a_command():
    zone = clean_zone_a() + [{"id": "abc; rm -rf /", "type": "A", "name": "x.example.com", "content": RELEASED_V4}]
    with pytest.raises(DNS.InputError, match="record id"):
        run(zone)


def test_allowlist_entry_without_reason_is_refused():
    want = desired(allowed_external_targets=[{"suffix": "x.example"}])
    with pytest.raises(DNS.InputError, match="reason"):
        run(clean_zone_a(), want=want)


def test_unparseable_a_content_is_dangling_not_crash():
    zone = clean_zone_a() + [rec(61, "A", "bad.example.com", "not-an-ip")]
    assert classes(run(zone)) == {"bad.example.com/A": "dangling"}


# --- CLI end to end ------------------------------------------------------------------


def write(tmp_path: Path, name: str, data) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data) if not name.endswith(".yaml") else yaml.safe_dump(data))
    return p


def cli(tmp_path: Path, zone_a: list[dict], extra: list[str] | None = None) -> subprocess.CompletedProcess:
    args = [
        sys.executable,
        str(SCRIPT),
        "check",
        "--desired",
        str(write(tmp_path, "desired.yaml", desired())),
        "--zone-export",
        f"example.com={write(tmp_path, 'a.json', zone_a)}",
        "--zone-export",
        f"example.net={write(tmp_path, 'b.json', clean_zone_b())}",
        "--hetzner",
        str(write(tmp_path, "servers.json", servers())),
        "--hetzner",
        str(write(tmp_path, "fips.json", floating())),
    ]
    return subprocess.run(args + (extra or []), capture_output=True, text=True, check=False)


def test_cli_clean_exit_0(tmp_path):
    out = cli(tmp_path, clean_zone_a())
    assert out.returncode == 0, out.stderr
    assert "CLEAN" in out.stdout


def test_cli_findings_exit_1_with_json_report(tmp_path):
    out = cli(tmp_path, clean_zone_a() + [rec(70, "A", "old.example.com", RELEASED_V4)], ["--json"])
    assert out.returncode == 1, out.stderr
    report = json.loads(out.stdout)
    assert report["status"] == "findings"
    assert report["findings"][0]["id"] == rid(70)


def test_cli_input_error_exit_2(tmp_path):
    bad = tmp_path / "a.json"
    bad.write_text("{not json")
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "check",
            "--desired",
            str(write(tmp_path, "desired.yaml", desired())),
            "--zone-export",
            f"example.com={bad}",
            "--hetzner",
            str(write(tmp_path, "servers.json", servers())),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 2
    assert "{not json" not in out.stderr  # input content is never echoed


# --- the committed desired state -------------------------------------------------------


def test_committed_desired_state_is_valid_and_kod_only():
    want = yaml.safe_load(DESIRED.read_text())
    DNS.validate_desired(want)
    assert want["zones"] == ["kodeme.io", "terakidz.com"]
    assert want["cloudflare_profile"] == "kodemeio"
    text = DESIRED.read_text().lower()
    for token in ("idtpp", "tpp-", "mac-"):
        assert token not in text, token
