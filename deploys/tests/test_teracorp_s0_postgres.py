"""Roadmap row 0.3 -- kod Postgres ports unpublished, Hetzner delete protection on.

Three offline checks: `ports` (static: renders the postgres compose's ports
with each kod manifest's env and refuses a non-loopback publish of
5432/6432/9187), `protection` (a `kctl-hz --json servers list` export against
the desired state), `probe` (TCP connect; tests use 127.0.0.1 listeners only).
Nothing here contacts Hetzner or a server.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import test_ci_gates
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ops/scripts/teracorp_s0_postgres.py"
PROTECTION = REPO_ROOT / "ops/wave0/s0/protection.kod.yaml"
MANIFESTS = REPO_ROOT / "deploys/instances/production"
SPEC = importlib.util.spec_from_file_location("teracorp_s0_postgres", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PG)

SIBLING_REPO = "kodemeio-postgres"
SIBLING = test_ci_gates.WORKSPACE_ROOT / SIBLING_REPO

# The ports stanzas of kodemeio-postgres' compose files, as they stand.
COMPOSE = """
services:
  postgres:
    image: example/postgres:16
    environment:
      PGBOUNCER_PORT: "6432"
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
  exporter:
    image: example/exporter:1
    ports:
      - "${POSTGRES_EXPORTER_PORT:-9187}:9187"
  pgbouncer:
    image: example/pgbouncer:1
    ports:
      - "${PGBOUNCER_PORT:-6432}:6432"
"""

LOOPBACK = {
    "POSTGRES_PORT": "127.0.0.1:5432",
    "PGBOUNCER_PORT": "127.0.0.1:6432",
    "POSTGRES_EXPORTER_PORT": "127.0.0.1:9187",
}


def setup_tree(tmp_path: Path, manifests: dict[str, dict], compose: str = COMPOSE) -> tuple[Path, Path]:
    inst = tmp_path / "deploys/instances/production"
    inst.mkdir(parents=True)
    bases = tmp_path / "deploys/bases"
    bases.mkdir(parents=True)
    (bases / "infra.yaml").write_text(yaml.safe_dump({"kind": "base", "env_defaults": {"TZ": "Asia/Jakarta"}}))
    for name, body in manifests.items():
        (inst / name).write_text(yaml.safe_dump(body))
    pg = tmp_path / "kodemeio-postgres"
    pg.mkdir()
    (pg / "docker-compose.yml").write_text(compose)
    (pg / "docker-compose.prod.yml").write_text(compose)
    return inst, pg


def pg_manifest(name: str, overrides: dict | None = None, **source) -> dict:
    body = {
        "kind": "instance",
        "extends": "../../bases/infra.yaml",
        "instance": {"name": name},
        "source_overrides": {"repo": "kodemeio-postgres", **source},
    }
    if overrides is not None:
        body["env_overrides"] = overrides
    return body


# --- port-spec parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("5432:5432", [("0.0.0.0", 5432)]),
        ("5432", [("0.0.0.0", 5432)]),
        ("127.0.0.1:5432:5432", [("127.0.0.1", 5432)]),
        ("127.0.0.1::5432", [("127.0.0.1", 5432)]),
        ("0.0.0.0:15432:5432/tcp", [("0.0.0.0", 5432)]),
        ("[::1]:5432:5432", [("::1", 5432)]),
        ("6430-6433:6430-6433", [("0.0.0.0", 6430), ("0.0.0.0", 6431), ("0.0.0.0", 6432), ("0.0.0.0", 6433)]),
        ({"target": 5432, "published": 5432}, [("0.0.0.0", 5432)]),
        ({"target": 5432, "published": 5432, "host_ip": "127.0.0.1"}, [("127.0.0.1", 5432)]),
    ],
)
def test_parse_port_spec(spec, expected):
    assert PG.parse_port(spec) == expected


def test_interpolation_follows_compose_rules():
    env = {"SET": "a", "EMPTY": ""}
    assert PG.interpolate("${SET:-x}", env) == "a"
    assert PG.interpolate("${EMPTY:-x}", env) == "x"
    assert PG.interpolate("${EMPTY-x}", env) == ""
    assert PG.interpolate("${UNSET-x}", env) == "x"
    assert PG.interpolate("$SET/${SET}", env) == "a/a"
    assert PG.interpolate("$$SET", env) == "$SET"
    with pytest.raises(PG.InputError, match="UNSET"):
        PG.interpolate("${UNSET}", env)


# --- ports: manifests x compose ------------------------------------------------------------


def test_default_compose_publishes_on_all_interfaces_and_fails(tmp_path):
    inst, pg = setup_tree(tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres")})
    findings = PG.check_ports(inst, pg)
    assert {(f["manifest"], f["container_port"], f["host_ip"]) for f in findings} == {
        ("kod-infra-postgres.yaml", 5432, "0.0.0.0"),
        ("kod-infra-postgres.yaml", 6432, "0.0.0.0"),
        ("kod-infra-postgres.yaml", 9187, "0.0.0.0"),
    }


def test_loopback_env_overrides_pass(tmp_path):
    inst, pg = setup_tree(
        tmp_path,
        {
            "kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres", LOOPBACK),
            "tkz-infra-postgres.yaml": pg_manifest(
                "tkz-infra-postgres", LOOPBACK, compose_path="./docker-compose.prod.yml"
            ),
        },
    )
    assert PG.check_ports(inst, pg) == []


def test_one_port_left_public_is_named(tmp_path):
    env = {**LOOPBACK, "PGBOUNCER_PORT": "6432"}
    inst, pg = setup_tree(tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres", env)})
    findings = PG.check_ports(inst, pg)
    assert [(f["compose"], f["service"], f["container_port"]) for f in findings] == [
        ("docker-compose.yml", "pgbouncer", 6432),
        ("docker-compose.prod.yml", "pgbouncer", 6432),
    ]


def test_explicit_all_interfaces_override_fails(tmp_path):
    env = {**LOOPBACK, "POSTGRES_PORT": "0.0.0.0:5432"}
    inst, pg = setup_tree(tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres", env)})
    assert [f["container_port"] for f in PG.check_ports(inst, pg)] == [5432, 5432]  # both compose files


def test_non_kod_manifests_are_out_of_scope(tmp_path):
    inst, pg = setup_tree(tmp_path, {"other-infra-postgres.yaml": pg_manifest("other-infra-postgres")})
    assert PG.check_ports(inst, pg) == []


def test_kod_manifest_not_using_postgres_repo_is_skipped(tmp_path):
    body = pg_manifest("kod-app-x")
    body["source_overrides"]["repo"] = "kodemeio-next"
    inst, pg = setup_tree(tmp_path, {"kod-app-x.yaml": body})
    assert PG.check_ports(inst, pg) == []


def test_both_candidate_compose_files_are_checked(tmp_path):
    inst, pg = setup_tree(tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres", LOOPBACK)})
    (pg / "docker-compose.prod.yml").write_text(COMPOSE.replace("${PGBOUNCER_PORT:-6432}:6432", "6432:6432"))
    findings = PG.check_ports(inst, pg)
    assert [(f["compose"], f["container_port"]) for f in findings] == [("docker-compose.prod.yml", 6432)]


def test_missing_compose_file_is_input_error(tmp_path):
    inst, pg = setup_tree(
        tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres", compose_path="./nope.yml")}
    )
    with pytest.raises(PG.InputError, match="nope.yml"):
        PG.check_ports(inst, pg)


def test_repo_compose_scan_flags_public_sensitive_ports_only(tmp_path):
    good = tmp_path / "a.yml"
    good.write_text(yaml.safe_dump({"services": {"t": {"ports": ["80:80", "127.0.0.1::5432"]}}}))
    bad = tmp_path / "b.yml"
    bad.write_text(yaml.safe_dump({"services": {"db": {"ports": ["6432:6432"]}}}))
    findings = PG.check_compose_files([good, bad], base=tmp_path)
    assert [(f["compose"], f["container_port"]) for f in findings] == [("b.yml", 6432)]


# --- the real repo --------------------------------------------------------------------------


def _require_sibling() -> Path:
    if (SIBLING / "docker-compose.prod.yml").is_file():
        return SIBLING
    verdict = test_ci_gates.missing_sibling_verdict(
        SIBLING_REPO, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI
    )
    if verdict == "fail":
        pytest.fail(f"{SIBLING_REPO} is listed in CI_GATES_REQUIRED_SIBLINGS but {SIBLING} does not exist")
    pytest.skip(f"{SIBLING_REPO} not checked out at {SIBLING}")
    raise AssertionError("unreachable")  # pragma: no cover


def test_real_kod_manifests_publish_no_postgres_port_publicly():
    findings = PG.check_ports(MANIFESTS, _require_sibling())
    assert findings == [], findings


def test_real_kod_postgres_manifests_carry_the_loopback_overrides():
    for name in ("kod-infra-postgres.yaml", "tkz-infra-postgres.yaml"):
        body = yaml.safe_load((MANIFESTS / name).read_text())
        assert body["env_overrides"] == LOOPBACK, name


def test_repo_compose_files_publish_no_postgres_port_publicly():
    assert PG.check_compose_files(PG.repo_compose_files(REPO_ROOT), base=REPO_ROOT) == []


# --- protection ----------------------------------------------------------------------------------


def server(name: str, delete: bool = True, rebuild: bool = True) -> dict:
    return {"id": abs(hash(name)) % 10_000, "name": name, "protection": {"delete": delete, "rebuild": rebuild}}


WANT = {
    "version": 1,
    "require": {"delete": True, "rebuild": True},
    "hetzner_profiles": {"kodemeio": ["kod-prod-01", "kod-prod-02"], "abcfood": ["abc-prod-02"]},
}


def exports(**over) -> dict:
    base = {
        "kodemeio": [server("kod-prod-01"), server("kod-prod-02")],
        "abcfood": [server("abc-prod-02"), server("abc-other")],
    }
    base.update(over)
    return base


def test_protection_all_on_passes_and_extra_servers_are_informational():
    report = PG.check_protection(WANT, exports())
    assert report["status"] == "protected"
    assert report["failures"] == []
    assert report["unlisted"] == ["abcfood:abc-other"]


def test_protection_off_fails():
    report = PG.check_protection(WANT, exports(kodemeio=[server("kod-prod-01", delete=False), server("kod-prod-02")]))
    assert report["status"] == "unprotected"
    assert report["failures"] == [
        {"profile": "kodemeio", "server": "kod-prod-01", "problem": "protection.delete is not true"}
    ]


def test_rebuild_protection_is_required_too():
    report = PG.check_protection(WANT, exports(abcfood=[server("abc-prod-02", rebuild=False)]))
    assert [f["problem"] for f in report["failures"]] == ["protection.rebuild is not true"]


def test_missing_server_fails():
    report = PG.check_protection(WANT, exports(kodemeio=[server("kod-prod-01")]))
    assert report["failures"] == [{"profile": "kodemeio", "server": "kod-prod-02", "problem": "not found in export"}]


def test_missing_protection_block_fails_closed():
    s = server("kod-prod-02")
    del s["protection"]
    report = PG.check_protection(WANT, exports(kodemeio=[server("kod-prod-01"), s]))
    assert report["status"] == "unprotected"


def test_missing_profile_export_is_input_error():
    with pytest.raises(PG.InputError, match="abcfood"):
        PG.check_protection(WANT, {"kodemeio": exports()["kodemeio"]})


def test_committed_protection_desired_state_is_valid_and_kod_only():
    want = yaml.safe_load(PROTECTION.read_text())
    PG.validate_protection(want)
    assert set(want["hetzner_profiles"]) == {"kodemeio", "abcfood"}
    assert "kod-prod-01" in want["hetzner_profiles"]["kodemeio"]
    assert "abc-prod-02" in want["hetzner_profiles"]["abcfood"]
    text = PROTECTION.read_text().lower()
    for token in ("idtpp", "tpp-", "mac-"):
        assert token not in text, token


# --- probe ------------------------------------------------------------------------------------------


@pytest.fixture
def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_probe_open_port_fails_closed_port_passes(listener):
    closed = closed_port()
    report = PG.probe("127.0.0.1", [listener, closed], timeout=1.0, allow_local=True)
    assert report["status"] == "exposed"
    assert report["ports"] == {str(listener): "open", str(closed): "closed"}
    assert PG.probe("127.0.0.1", [closed], timeout=1.0, allow_local=True)["status"] == "not-exposed"


def test_probe_refuses_local_target_without_flag():
    with pytest.raises(PG.InputError, match="outside"):
        PG.probe("127.0.0.1", [5432], timeout=1.0)


# --- CLI ---------------------------------------------------------------------------------------------


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def test_cli_ports_exit_codes(tmp_path):
    inst, pg = setup_tree(tmp_path, {"kod-infra-postgres.yaml": pg_manifest("kod-infra-postgres")})
    bad = cli("ports", "--manifests", str(inst), "--postgres-repo", str(pg), "--no-repo-scan")
    assert bad.returncode == 1
    assert "5432" in bad.stdout
    (inst / "kod-infra-postgres.yaml").write_text(yaml.safe_dump(pg_manifest("kod-infra-postgres", LOOPBACK)))
    ok = cli("ports", "--manifests", str(inst), "--postgres-repo", str(pg), "--no-repo-scan")
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_cli_protection_and_probe(tmp_path, listener):
    want = tmp_path / "want.yaml"
    want.write_text(yaml.safe_dump(WANT))
    args = ["protection", "--desired", str(want)]
    for profile, servers in exports().items():
        p = tmp_path / f"{profile}.json"
        p.write_text(json.dumps(servers))
        args += ["--export", f"{profile}={p}"]
    assert cli(*args).returncode == 0
    probe = cli("probe", "127.0.0.1", "--ports", str(listener), "--allow-local", "--timeout", "1")
    assert probe.returncode == 1
    assert "EXPOSED" in probe.stdout
    assert cli("probe", "127.0.0.1", "--ports", "5432").returncode == 2
