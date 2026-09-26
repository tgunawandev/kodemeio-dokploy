"""Drift test: the kod estate's backup inventory (ops/backup-inventory.kod.yaml)
against the real kod-*.sh job scripts in kodemeio-skills (docker/jobs/).

The inventory is the desired-state description of what backs up what, at what
cadence, and where the offsite copy lives. The job scripts are the ONLY thing
that actually runs. If they drift apart -- a prefix renamed in one but not the
other, a threshold changed in the job but not documented, an item quietly
dropped -- nobody notices until a restore needs it and it isn't there. This
module reads both and asserts they still agree.

Sibling handling is `deploys/tests/test_ci_gates.py`'s own pattern, reused
directly (`missing_sibling_verdict`, `CI`, `REQUIRED_IN_CI`), not reinvented:
kodemeio-skills absent locally -> skip (this repo's own CI then checks only
the inventory's shape); absent AND CI=true AND "kodemeio-skills" listed in
CI_GATES_REQUIRED_SIBLINGS -> fail, a checkout defect, not nothing-to-check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import test_ci_gates
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
INVENTORY_PATH = REPO_ROOT / "ops" / "backup-inventory.kod.yaml"

SIBLING_REPO = "kodemeio-skills"
JOBS_DIR = test_ci_gates.WORKSPACE_ROOT / SIBLING_REPO / "docker" / "jobs"

# odoo-db <-> odoo-filestore is the only kind pair this inventory requires.
ODOO_PAIR_KINDS = {"odoo-db": "odoo-filestore", "odoo-filestore": "odoo-db"}
# Kinds the two freshness jobs actually check per-prefix (excludes gap,
# app-dump, and bucket, none of which the b2-fresh/hz-fresh loops cover).
FRESH_ELIGIBLE_KINDS = {"odoo-db", "odoo-filestore", "pg-db"}
FORBIDDEN_TOKENS = ("tpp", "mac", "idtpp")


# --- loading ------------------------------------------------------------------


def _load_inventory() -> dict:
    return yaml.safe_load(INVENTORY_PATH.read_text())


def _require_job_scripts() -> Path:
    """The kodemeio-skills jobs dir, or skip/fail per test_ci_gates' own
    sibling-repo convention."""
    if JOBS_DIR.is_dir():
        return JOBS_DIR
    verdict = test_ci_gates.missing_sibling_verdict(
        SIBLING_REPO, ci=test_ci_gates.CI, required=test_ci_gates.REQUIRED_IN_CI
    )
    if verdict == "fail":
        pytest.fail(
            f"{SIBLING_REPO} is listed in CI_GATES_REQUIRED_SIBLINGS but "
            f"{test_ci_gates.WORKSPACE_ROOT / SIBLING_REPO} does not exist -- checkout defect, not nothing-to-check"
        )
    pytest.skip(
        f"{SIBLING_REPO} not checked out locally at {test_ci_gates.WORKSPACE_ROOT / SIBLING_REPO} -- "
        "this repo's own CI checks the inventory shape only"
    )
    raise AssertionError("unreachable")  # pragma: no cover


def _extract_block(text: str, begin_marker: str, end_marker: str) -> str:
    start = text.index(begin_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def offsite_mirror_sources(jobs_dir: Path) -> list[str]:
    """`b2_sync <source>` arguments between OFFSITE-PREFIXES-BEGIN/END in
    kod-offsite-mirror.sh -- the real sources the mirror job copies to
    kod-prod-backup/<source>/..."""
    text = (jobs_dir / "kod-offsite-mirror.sh").read_text()
    block = _extract_block(text, "OFFSITE-PREFIXES-BEGIN", "OFFSITE-PREFIXES-END")
    return re.findall(r"^\s*b2_sync\s+(\S+)", block, re.MULTILINE)


def offsite_fresh_prefixes(jobs_dir: Path) -> dict[str, int]:
    """`b2_fresh <prefix> <hours>` pairs between FRESH-PREFIXES-BEGIN/END in
    kod-offsite-fresh.sh (checked against the kod-prod-backup B2 mirror)."""
    text = (jobs_dir / "kod-offsite-fresh.sh").read_text()
    block = _extract_block(text, "FRESH-PREFIXES-BEGIN", "FRESH-PREFIXES-END")
    pairs = re.findall(r"^\s*b2_fresh\s+(\S+)\s+(\d+)", block, re.MULTILINE)
    return {prefix: int(hours) for prefix, hours in pairs}


def primary_fresh_prefixes(jobs_dir: Path) -> dict[tuple[str, str], int]:
    """`hzfresh <bucket> <prefix> <hours>` triples between
    PRIMARY-PREFIXES-BEGIN/END in kod-hz-fresh.sh (checked directly against
    the primary Hetzner buckets)."""
    text = (jobs_dir / "kod-hz-fresh.sh").read_text()
    block = _extract_block(text, "PRIMARY-PREFIXES-BEGIN", "PRIMARY-PREFIXES-END")
    triples = re.findall(r"^\s*hzfresh\s+(\S+)\s+(\S+)\s+(\d+)", block, re.MULTILINE)
    return {(bucket, prefix): int(hours) for bucket, prefix, hours in triples}


# --- pure check functions (operate on an in-memory item list, so the mutation
#     test below can run them against a deliberately-broken copy) -------------


def pair_violations(items: list[dict]) -> list[str]:
    by_id = {item["id"]: item for item in items}
    violations: list[str] = []
    for item in items:
        expected_partner_kind = ODOO_PAIR_KINDS.get(item["kind"])
        if expected_partner_kind is None:
            continue
        pair_id = item.get("pair")
        if not pair_id:
            violations.append(f"{item['id']}: kind {item['kind']!r} has no 'pair'")
            continue
        partner = by_id.get(pair_id)
        if partner is None:
            violations.append(f"{item['id']}: pair {pair_id!r} does not exist in the inventory")
            continue
        if partner["kind"] != expected_partner_kind:
            violations.append(
                f"{item['id']}: pair {pair_id!r} has kind {partner['kind']!r}, expected {expected_partner_kind!r}"
            )
        if partner.get("pair") != item["id"]:
            violations.append(f"{item['id']}: pair {pair_id!r} does not point back (got {partner.get('pair')!r})")
    return violations


def non_gap_field_violations(items: list[dict]) -> list[str]:
    violations: list[str] = []
    for item in items:
        if item["kind"] == "gap":
            continue
        if not item.get("primary"):
            violations.append(f"{item['id']}: missing 'primary'")
        offsite = item.get("offsite")
        if not isinstance(offsite, dict) or not offsite.get("bucket") or not offsite.get("prefix"):
            violations.append(f"{item['id']}: missing 'offsite.bucket'/'offsite.prefix'")
        if not item.get("fresh_h"):
            violations.append(f"{item['id']}: missing 'fresh_h'")
    return violations


def offsite_coverage_violations(items: list[dict], mirror_sources: list[str]) -> list[str]:
    violations: list[str] = []
    for item in items:
        if item["kind"] in ("gap", "app-dump") or item.get("planned"):
            continue
        prefix = item["offsite"]["prefix"]
        if not any(prefix.startswith(source) for source in mirror_sources):
            violations.append(f"{item['id']}: offsite.prefix {prefix!r} matches no b2_sync source in {mirror_sources}")
    return violations


def fresh_equality_violations(
    items: list[dict],
    job_offsite: dict[str, int],
    job_primary: dict[tuple[str, str], int],
) -> list[str]:
    violations: list[str] = []
    candidates = [item for item in items if item["kind"] in FRESH_ELIGIBLE_KINDS and not item.get("planned")]

    inv_offsite = {item["offsite"]["prefix"]: item["fresh_h"] for item in candidates}
    only_inv = set(inv_offsite) - set(job_offsite)
    only_job = set(job_offsite) - set(inv_offsite)
    if only_inv or only_job:
        violations.append(f"offsite prefixes differ: inventory-only={sorted(only_inv)} job-only={sorted(only_job)}")
    else:
        for prefix, hours in job_offsite.items():
            if inv_offsite[prefix] != hours:
                violations.append(f"offsite {prefix!r}: inventory fresh_h={inv_offsite[prefix]} != job {hours}")

    inv_primary = {(item["primary"]["bucket"], item["primary"]["prefix"]): item["fresh_h"] for item in candidates}
    only_inv_p = set(inv_primary) - set(job_primary)
    only_job_p = set(job_primary) - set(inv_primary)
    if only_inv_p or only_job_p:
        violations.append(
            f"primary bucket/prefix differ: inventory-only={sorted(only_inv_p)} job-only={sorted(only_job_p)}"
        )
    else:
        for key, hours in job_primary.items():
            if inv_primary[key] != hours:
                violations.append(f"primary {key!r}: inventory fresh_h={inv_primary[key]} != job {hours}")

    return violations


def idtpp_violations(raw_text: str) -> list[str]:
    low = raw_text.lower()
    return [token for token in FORBIDDEN_TOKENS if token in low]


# --- tests: shape only (never need the sibling repo) --------------------------


def test_every_odoo_db_has_filestore_pair():
    items = _load_inventory()["items"]
    violations = pair_violations(items)
    assert not violations, violations


def test_non_gap_items_have_primary_offsite_fresh():
    items = _load_inventory()["items"]
    violations = non_gap_field_violations(items)
    assert not violations, violations


def test_every_planned_item_has_notes():
    items = _load_inventory()["items"]
    missing = [item["id"] for item in items if item.get("planned") and not item.get("notes")]
    assert not missing, f"planned items missing 'notes': {missing}"


def test_no_idtpp_references():
    violations = idtpp_violations(INVENTORY_PATH.read_text())
    assert not violations, f"forbidden tokens found in {INVENTORY_PATH}: {violations}"


# --- tests: drift against the real job scripts (skip/fail per sibling rule) --


def test_offsite_mirror_sources_cover_inventory():
    jobs_dir = _require_job_scripts()
    items = _load_inventory()["items"]
    mirror_sources = offsite_mirror_sources(jobs_dir)
    assert mirror_sources, "no b2_sync sources parsed from kod-offsite-mirror.sh -- marker text changed?"
    violations = offsite_coverage_violations(items, mirror_sources)
    assert not violations, violations


def test_fresh_prefixes_equal_inventory():
    jobs_dir = _require_job_scripts()
    items = _load_inventory()["items"]
    job_offsite = offsite_fresh_prefixes(jobs_dir)
    job_primary = primary_fresh_prefixes(jobs_dir)
    assert job_offsite, "no b2_fresh prefixes parsed from kod-offsite-fresh.sh -- marker text changed?"
    assert job_primary, "no hzfresh prefixes parsed from kod-hz-fresh.sh -- marker text changed?"
    violations = fresh_equality_violations(items, job_offsite, job_primary)
    assert not violations, violations


# --- mutation check (the brief's W4 manual step, made a permanent test) ------


def test_mutation_deleting_hrms_filestore_fails_pair_and_coverage_checks():
    """The brief's Step 2 mutation check ("delete the hrms filestore item ->
    test fails; restore"), automated: delete `odoo-hrms-filestore` from an
    in-memory copy of the inventory and assert BOTH the pair check and the
    fresh-prefix coverage/equality check catch it -- the pair check because
    `odoo-hrms-db.pair` now points at nothing, the coverage check because the
    inventory silently stops declaring a prefix the running job still checks.
    Proves the checks have teeth, not just that today's file happens to pass.
    """
    jobs_dir = _require_job_scripts()
    items = _load_inventory()["items"]
    mutated = [item for item in items if item["id"] != "odoo-hrms-filestore"]

    pair_result = pair_violations(mutated)
    assert pair_result, "deleting odoo-hrms-filestore must fail the pair check"
    assert any("odoo-hrms-db" in v for v in pair_result), pair_result

    job_offsite = offsite_fresh_prefixes(jobs_dir)
    job_primary = primary_fresh_prefixes(jobs_dir)
    coverage_result = fresh_equality_violations(mutated, job_offsite, job_primary)
    assert coverage_result, "deleting odoo-hrms-filestore must fail the fresh-prefix coverage check"
