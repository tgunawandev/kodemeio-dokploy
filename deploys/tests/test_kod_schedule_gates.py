"""R8/G2 + P4 gate: every kod estate schedule ships DISABLED.

`deploys/instances/production/kod-infra-kctl.yaml` declares kod-offsite-mirror,
kod-hz-fresh and kod-offsite-fresh (Wave 0) and the four kod-metrics-* jobs
(P4, observability with redaction) with `enabled: false`. That is the machine
half of ledger ruling R8: the schedules stay off until the founder's gates hold
-- G2 (a B2 key without deleteFiles, a lifecycle rule, and a hard-delete test
that returns 401, measured on the KOD keys), a fresh `kod_odoo_hrms` dump, and
the confirmed erp/hrms restic repos -- and until G5/G6 have deployed the
backups the two freshness jobs check. Before this file the gate existed only as
a sentence in the manifest comment: nothing failed if someone flipped a flag or
dropped the key.

Wave 0 final review M3. When a schedule is genuinely flipped on, this test
fails BY DESIGN; add the name to `ENABLED_BY_EXCEPTION` with the gate evidence
in the same commit, so the flip is a reviewed decision rather than a silent one.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "instances" / "production" / "kod-infra-kctl.yaml"

# The kod estate's jobs. A rename is a deploy-time 404 inside Dokploy's
# scheduler (`jobrun <name>`), which has no failure notification -- so pin it.
# The four kod-metrics-* names were added by P4 (observability with redaction)
# and ship disabled for their own gate (manifest comment: image pin, the four
# Healthchecks checks, LiteLLM deployed, a Hatchet token, an Odoo read-only
# operator key + sql_guard, the confirmed thresholds).
KOD_SCHEDULES = {
    "kod-offsite-mirror",
    "kod-hz-fresh",
    "kod-offsite-fresh",
    "kod-metrics-token-cost",
    "kod-metrics-queue-lag",
    "kod-metrics-outcome",
    "kod-metrics-funnel",
}

# name -> why enabling it is deliberate, with the evidence. Empty on purpose:
# every kod schedule is gated today (R8/G2 for the backup chain, the P4 gate
# above for the metrics jobs).
ENABLED_BY_EXCEPTION: dict[str, str] = {}


def _schedules() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text()).get("schedules") or []


def test_manifest_declares_every_kod_schedule():
    names = {s["name"] for s in _schedules()}
    assert names == KOD_SCHEDULES, f"kod-infra-kctl schedules changed: {sorted(names)}"


def test_every_schedule_states_enabled_explicitly_and_it_is_false():
    """The key must be PRESENT, not merely truthy-checked: a schedule that
    omits `enabled` is a schedule Dokploy may run (the field it applies), which
    is exactly the state R8 says the estate must not be in."""
    for sched in _schedules():
        assert "enabled" in sched, f"{sched['name']}: no explicit 'enabled' key -- Dokploy may default it on"
        assert sched["enabled"] is False, (
            f"{sched['name']}: enabled={sched['enabled']!r}; R8 keeps all three off until G2 + the "
            f"hrms dump + the erp/hrms restic repos hold. If this flip is deliberate, add "
            f"{sched['name']!r} to ENABLED_BY_EXCEPTION with its gate evidence (Wave 0 final review M3)."
        )


def test_no_schedule_is_enabled_without_an_exception_entry():
    for sched in _schedules():
        if sched.get("enabled") is True:
            assert sched["name"] in ENABLED_BY_EXCEPTION, f"{sched['name']} is enabled with no recorded reason/evidence"


def test_every_schedule_has_a_five_field_cron():
    for sched in _schedules():
        fields = sched["cron"].split()
        assert len(fields) == 5, f"{sched['name']}: cron {sched['cron']!r} is not a 5-field UTC cron"


def test_mirror_runs_before_the_two_freshness_jobs():
    """The mirror is the only writer; a freshness job that ran first would
    report STALE for a copy that was about to land. The manifest's own comment
    spells this out (45 */2 -> 23:40/23:50 UTC)."""
    by_name = {s["name"]: s["cron"] for s in _schedules()}
    mirror_minute, mirror_hour_step = by_name["kod-offsite-mirror"].split()[:2]
    assert mirror_minute.isdigit(), f"mirror cron {by_name['kod-offsite-mirror']!r} must have a literal minute"
    assert re.fullmatch(r"\*/[0-9]+", mirror_hour_step), (
        f"mirror cron {by_name['kod-offsite-mirror']!r} must run on a fixed interval"
    )
    # 22:45 UTC is the last mirror run before the 23:40 / 23:50 checks.
    assert int(mirror_minute) < 60
    for fresh in ("kod-hz-fresh", "kod-offsite-fresh"):
        minute, hour = by_name[fresh].split()[:2]
        assert (int(hour), int(minute)) > (22, 45), (
            f"{fresh} at {by_name[fresh]!r} must run after the mirror's last pre-check run (22:45 UTC)"
        )
