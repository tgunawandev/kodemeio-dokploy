"""Tests for the filestore-backup instance generator.

The volume name is the whole safety story here: Dokploy names compose volumes
{appName}_{volume}, so decoy filestore volumes sit beside the live one and
picking the wrong one produces a small, successful, useless backup.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TENANT = {"code": "tpp", "name": "Pakerti", "domain": "idtpp.com"}
ENTRY = {
    "short": "erp",
    "volume": "compose-quantify-cross-platform-panel-898kcx_odoo-filestore",
    "database": "tpp_odoo_erp",
    "min_files": 36000,
    "pinned_cron": "50 2 * * *",
    "bucket": "hz-tpp-odoo-filestore",
    "server": "tpp-prod-03",
}


def test_pins_the_exact_volume_and_server():
    from generate import gen_filestore_backup

    y_name, y_content, e_name, e_content = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert y_name == "tpp-odoo-filestore-backup.yaml"
    assert "compose-quantify-cross-platform-panel-898kcx_odoo-filestore" in y_content
    assert "server: tpp-prod-03" in y_content
    assert "FILESTORE_DB: tpp_odoo_erp" in y_content
    assert "FILESTORE_MIN_FILES: '36000'" in y_content


def test_repository_url_is_scoped_per_instance():
    """Each instance gets its own restic repo prefix, so a restore or a
    compromise of one cannot reach the other."""
    from generate import gen_filestore_backup

    _, y_content, _, _ = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert "s3:https://fsn1.your-objectstorage.com/hz-tpp-odoo-filestore/tpp-odoo-erp" in y_content


def test_pinned_cron_is_carried_through():
    """The pinned run is what guarantees a filestore snapshot NEWER than the
    nightly pg_dump it will be restored alongside."""
    from generate import gen_filestore_backup

    _, y_content, _, _ = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert "BACKUP_CRON_PINNED: 50 2 * * *" in y_content


STAGING_ENTRY = {
    "short": "erp",
    "environment": "staging",
    "volume": "compose-compress-virtual-program-t5pjl6_odoo-filestore",
    "database": "tpp_odoo_erp_stg",
    "min_files": 490,
    "bucket": "hz-tpp-odoo-filestore",
    "server": "tpp-prod-07",
}


def test_staging_entry_gets_a_suffixed_name_and_its_own_repo():
    """Staging must never share a restic repository with production."""
    from generate import gen_filestore_backup

    y_name, y_content, e_name, _ = gen_filestore_backup(TENANT, STAGING_ENTRY, "staging", "tpp-prod-07", suffix="-stg")

    assert y_name == "tpp-odoo-filestore-backup-stg.yaml"
    assert "name: tpp-odoo-filestore-backup-stg" in y_content
    assert "hz-tpp-odoo-filestore/tpp-odoo-erp-stg" in y_content
    assert "FILESTORE_DB: tpp_odoo_erp_stg" in y_content
    assert "compose-compress-virtual-program-t5pjl6_odoo-filestore" in y_content
    assert e_name == ".env.tpp-odoo-filestore-backup-stg.example"


def test_staging_repo_differs_from_production_repo():
    from generate import gen_filestore_backup

    _, prod, _, _ = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")
    _, stg, _, _ = gen_filestore_backup(TENANT, STAGING_ENTRY, "staging", "tpp-prod-07", suffix="-stg")

    def repo(text):
        for line in text.splitlines():
            if "RESTIC_REPOSITORY:" in line:
                return line.split("RESTIC_REPOSITORY:", 1)[1].strip()
        raise AssertionError("no RESTIC_REPOSITORY")

    assert repo(prod) != repo(stg)


def test_env_example_never_carries_a_real_password():
    from generate import gen_filestore_backup

    _, _, e_name, e_content = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert e_name == ".env.tpp-odoo-filestore-backup.example"
    assert "RESTIC_PASSWORD=CHANGEME" in e_content
    assert "AWS_ACCESS_KEY_ID=CHANGEME" in e_content
    # the operator must be told where the real one lives
    assert "1Password" in e_content


MAC = {"code": "mac", "name": "Mandiri Agro", "domain": "idtpp.com"}
MAC_ERP = {
    "short": "erp",
    "instance_short": "erp",
    "volume": "compose-calculate-neural-matrix-phtlsl_odoo-filestore",
    "database": "mac_odoo_erp",
    "min_files": 1000,
    "pinned_cron": "20 1 * * *",
    "bucket": "hz-mac-odoo-filestore",
    "server": "tpp-prod-02",
}
MAC_HRMS = {
    "short": "hrms",
    "instance_short": "hrms",
    "volume": "compose-bypass-cross-platform-interface-j4al4h_odoo-filestore",
    "database": "mac_odoo_hrms",
    "min_files": 500,
    "pinned_cron": "25 1 * * *",
    "bucket": "hz-mac-odoo-filestore",
    "server": "tpp-prod-02",
}


def test_two_production_entries_do_not_collide():
    """mac is the first tenant with two production filestore backups. Without
    instance_short both would generate mac-odoo-filestore-backup.yaml and the
    same env filename, and the write loop in main() would keep only the last --
    leaving one repository that silently never exists while the deploy reports
    success."""
    from generate import gen_filestore_backup

    erp_yaml, _, erp_env, _ = gen_filestore_backup(MAC, MAC_ERP, "production", "tpp-prod-02")
    hrms_yaml, _, hrms_env, _ = gen_filestore_backup(MAC, MAC_HRMS, "production", "tpp-prod-02")

    assert erp_yaml == "mac-odoo-erp-filestore-backup.yaml"
    assert hrms_yaml == "mac-odoo-hrms-filestore-backup.yaml"
    assert erp_yaml != hrms_yaml
    assert erp_env != hrms_env


def test_instance_short_absent_keeps_the_existing_name():
    """tpp and tpp25 must not be renamed -- a rename would orphan their live
    Dokploy composes and their already-seeded restic repositories."""
    from generate import gen_filestore_backup

    y_name, _, e_name, _ = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert y_name == "tpp-odoo-filestore-backup.yaml"
    assert e_name == ".env.tpp-odoo-filestore-backup.example"


def test_mac_entries_get_separate_repositories():
    from generate import gen_filestore_backup

    _, erp_content, _, _ = gen_filestore_backup(MAC, MAC_ERP, "production", "tpp-prod-02")
    _, hrms_content, _, _ = gen_filestore_backup(MAC, MAC_HRMS, "production", "tpp-prod-02")

    assert "hz-mac-odoo-filestore/mac-odoo-erp" in erp_content
    assert "hz-mac-odoo-filestore/mac-odoo-hrms" in hrms_content


KOD = {"code": "kod", "name": "Kodemeio", "domain": "kodeme.io"}
KOD_DESK = {
    "short": "desk",
    "instance_short": "desk",
    "project": "apps",
    "volume": "compose-input-wireless-system-hvoyqp_odoo-filestore",
    "database": "kod_odoo_desk",
    "min_files": 440,
    "pinned_cron": "20 22 * * *",
    "bucket": "kodemeio-odoo-filestore",
    "server": "abc-prod-02",
}


def test_project_override_is_the_difference_between_deployable_and_not():
    """kod's Dokploy has projects web/database/apps and NO project named `kod`.

    `deploy setup` resolves the project by name and REFUSES when it is missing
    (ensure_project_and_env raises "Project 'kod' not found"); it never creates
    one. So without this override the backup is not merely misnamed, it cannot
    be deployed at all."""
    from generate import gen_filestore_backup

    _, content, _, _ = gen_filestore_backup(KOD, KOD_DESK, "production", "abc-prod-02")

    assert "project: apps" in content
    assert "project: kod" not in content


def test_project_defaults_to_the_tenant_code_when_absent():
    """Every existing entry must keep resolving to its own project: a silent
    switch to a shared project would put tpp's backup in someone else's tree."""
    from generate import gen_filestore_backup

    _, content, _, _ = gen_filestore_backup(TENANT, ENTRY, "production", "tpp-prod-03")

    assert "project: tpp" in content


def test_kod_desk_is_named_for_the_instance_not_the_tenant():
    """The kod estate has three live Odoo composes but ONE entry so far, so the
    instance name must carry the instance (`desk`), never collapse to
    kod-odoo-filestore-backup -- which would claim the whole estate."""
    from generate import gen_filestore_backup

    y_name, content, e_name, _ = gen_filestore_backup(KOD, KOD_DESK, "production", "abc-prod-02")

    assert y_name == "kod-odoo-desk-filestore-backup.yaml"
    assert e_name == ".env.kod-odoo-desk-filestore-backup.example"
    assert "s3:https://fsn1.your-objectstorage.com/kodemeio-odoo-filestore/kod-odoo-desk" in content
    assert "compose-input-wireless-system-hvoyqp_odoo-filestore" in content
    assert "FILESTORE_DB: kod_odoo_desk" in content
    assert "server: abc-prod-02" in content
