#!/usr/bin/env bash
#
# refresh-mac-staging.sh
#
# One-command refresh of MAC staging databases from production.
#
# Steps:
#   1. Dump prod databases (mac_odoo_erp, mac_odoo_hrms) to ~/backups/mac-stg-clone/
#   2. Drop + recreate staging databases (stg_mac_odoo_erp, stg_mac_odoo_hrms)
#   3. Restore prod dumps into staging
#   4. Pre-flight update web.base.url so Odoo boots with correct links
#   5. Redeploy Dokploy compose services (mac-odoo-erp-stg, mac-odoo-hrms-stg)
#   6. Wait 90s for containers to become ready
#   7. Run staging neutralization via kctl-odoo
#   8. Smoke-test HTTPS endpoints
#   9. Prune old backups (keep 5 most recent per database)
#
# Assumes kctl-pg, kctl-dokploy, kctl-odoo, and kctl-op are on PATH.
#
# Historical design: docs/archive/superpowers/plans/2026-04-10-mac-staging-clone.md

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROD_ERP_DB="mac_odoo_erp"
PROD_HRMS_DB="mac_odoo_hrms"

STG_ERP_DB="stg_mac_odoo_erp"
STG_HRMS_DB="stg_mac_odoo_hrms"

STG_ERP_URL="https://mac-erp-stg.idtpp.com"
STG_HRMS_URL="https://mac-hrms-stg.idtpp.com"

DOKPLOY_ERP_APP="mac-odoo-erp-stg"
DOKPLOY_HRMS_APP="mac-odoo-hrms-stg"
DOKPLOY_PROFILE="${KCTL_DOKPLOY_PROFILE:?Set KCTL_DOKPLOY_PROFILE explicitly}"

KCTL_ODOO_ERP_PROFILE="mac-erp-stg"
KCTL_ODOO_HRMS_PROFILE="mac-hrms-stg"

BACKUP_DIR="${HOME}/backups/mac-stg-clone"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

ERP_DUMP="${BACKUP_DIR}/${PROD_ERP_DB}_${TIMESTAMP}.sql.gz"
HRMS_DUMP="${BACKUP_DIR}/${PROD_HRMS_DB}_${TIMESTAMP}.sql.gz"

WAIT_SECONDS=90

log() {
    echo "==> [$(date +%H:%M:%S)] $*"
}

resolve_compose_id() {
    local app_name="$1"
    local matches
    matches="$(
        kctl-dokploy -p "${DOKPLOY_PROFILE}" --quiet --json \
            compose search --name "${app_name}"
    )"
    jq -er --arg name "${app_name}" '
        [.[] | select(.name == $name)]
        | if length == 1 then .[0].composeId
          else error("expected exactly one compose named " + $name)
          end
    ' <<<"${matches}"
}

# ---------------------------------------------------------------------------
# 0. Preparation
# ---------------------------------------------------------------------------

log "Starting MAC staging refresh (timestamp: ${TIMESTAMP})"
mkdir -p "${BACKUP_DIR}"

# ---------------------------------------------------------------------------
# 1. Dump prod databases
# ---------------------------------------------------------------------------

log "Dumping prod database ${PROD_ERP_DB} -> ${ERP_DUMP}"
kctl-pg backup dump --database "${PROD_ERP_DB}" --output "${ERP_DUMP}"

log "Dumping prod database ${PROD_HRMS_DB} -> ${HRMS_DUMP}"
kctl-pg backup dump --database "${PROD_HRMS_DB}" --output "${HRMS_DUMP}"

# ---------------------------------------------------------------------------
# 2. Drop + recreate staging databases
# ---------------------------------------------------------------------------

log "Dropping staging database ${STG_ERP_DB} (ignore errors if missing)"
kctl-pg db drop --database "${STG_ERP_DB}" --yes || true

log "Dropping staging database ${STG_HRMS_DB} (ignore errors if missing)"
kctl-pg db drop --database "${STG_HRMS_DB}" --yes || true

log "Creating staging database ${STG_ERP_DB}"
kctl-pg db create --database "${STG_ERP_DB}"

log "Creating staging database ${STG_HRMS_DB}"
kctl-pg db create --database "${STG_HRMS_DB}"

# ---------------------------------------------------------------------------
# 3. Restore prod dumps into staging
# ---------------------------------------------------------------------------

log "Restoring ${ERP_DUMP} -> ${STG_ERP_DB}"
kctl-pg backup restore --database "${STG_ERP_DB}" --input "${ERP_DUMP}"

log "Restoring ${HRMS_DUMP} -> ${STG_HRMS_DB}"
kctl-pg backup restore --database "${STG_HRMS_DB}" --input "${HRMS_DUMP}"

# ---------------------------------------------------------------------------
# 4. Pre-flight: update web.base.url before Odoo boots
# ---------------------------------------------------------------------------

log "Updating web.base.url on ${STG_ERP_DB} -> ${STG_ERP_URL}"
kctl-pg query --database "${STG_ERP_DB}" \
    "UPDATE ir_config_parameter SET value='${STG_ERP_URL}' WHERE key='web.base.url';"

log "Updating web.base.url on ${STG_HRMS_DB} -> ${STG_HRMS_URL}"
kctl-pg query --database "${STG_HRMS_DB}" \
    "UPDATE ir_config_parameter SET value='${STG_HRMS_URL}' WHERE key='web.base.url';"

# ---------------------------------------------------------------------------
# 5. Redeploy Dokploy compose services
# ---------------------------------------------------------------------------

log "Redeploying Dokploy compose ${DOKPLOY_ERP_APP}"
ERP_COMPOSE_ID="$(resolve_compose_id "${DOKPLOY_ERP_APP}")"
kctl-dokploy -p "${DOKPLOY_PROFILE}" compose redeploy "${ERP_COMPOSE_ID}"

log "Redeploying Dokploy compose ${DOKPLOY_HRMS_APP}"
HRMS_COMPOSE_ID="$(resolve_compose_id "${DOKPLOY_HRMS_APP}")"
kctl-dokploy -p "${DOKPLOY_PROFILE}" compose redeploy "${HRMS_COMPOSE_ID}"

# ---------------------------------------------------------------------------
# 6. Wait for containers to become ready
# ---------------------------------------------------------------------------

log "Waiting ${WAIT_SECONDS}s for containers to become ready"
sleep "${WAIT_SECONDS}"

# ---------------------------------------------------------------------------
# 7. Neutralize staging
# ---------------------------------------------------------------------------

log "Fetching staging admin password from 1Password"
STG_ADMIN_PASSWORD="$(kctl-op read "op://kodemeio/mac-odoo/staging-admin-password")"
export STG_ADMIN_PASSWORD

log "Neutralizing ${KCTL_ODOO_ERP_PROFILE}"
kctl-odoo -p "${KCTL_ODOO_ERP_PROFILE}" staging neutralize-staging

log "Neutralizing ${KCTL_ODOO_HRMS_PROFILE}"
kctl-odoo -p "${KCTL_ODOO_HRMS_PROFILE}" staging neutralize-staging

# ---------------------------------------------------------------------------
# 8. Smoke test HTTPS endpoints
# ---------------------------------------------------------------------------

log "Smoke testing ${STG_ERP_URL}"
curl -fsS -I "${STG_ERP_URL}" >/dev/null

log "Smoke testing ${STG_HRMS_URL}"
curl -fsS -I "${STG_HRMS_URL}" >/dev/null

# ---------------------------------------------------------------------------
# 9. Clean up old backups (keep 5 most recent per database)
# ---------------------------------------------------------------------------

log "Pruning old backups in ${BACKUP_DIR} (keeping 5 most recent per DB)"
cd "${BACKUP_DIR}"
# shellcheck disable=SC2012  # ls -t is intentional here; filenames are timestamped and safe
ls -1t "${PROD_ERP_DB}"_*.sql.gz 2>/dev/null | tail -n +6 | xargs -r rm --
# shellcheck disable=SC2012
ls -1t "${PROD_HRMS_DB}"_*.sql.gz 2>/dev/null | tail -n +6 | xargs -r rm --

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

log "MAC staging refresh complete"
echo "==> Done in ${SECONDS}s"
