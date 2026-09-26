#!/usr/bin/env bash
# test_drill_odoo.sh — local end-to-end test of the Odoo restore drill
# (Teracorp Wave 0 Task 7, spec W6/W7).
#
# Positive (W6): a synthetic fixture (fresh Odoo, one confirmed sale order,
# 3 attachments with known content) is dumped + restic-backed-up into a
# local S3 stand-in, then restored by drill-odoo.sh into an isolated
# compose stack. Asserts status == ok, every step has a timing, rpo_seconds
# >= 0, validation.attachments_ok == 3, validation.egress_blocked == true.
#
# Negative (W7):
#   a) a second fixture with one attachment blob deleted before the restic
#      backup -> the drill must exit non-zero and name the missing
#      attachment id.
#   b) the DB dump re-uploaded under a later timestamp than the filestore
#      snapshot -> results.json must carry a "pairing: filestore older
#      than dump" warning.
#
# Skips (exit 0, clear message) if Docker is unavailable. Never touches the
# shared kodemeio-odoo dev stack, its databases or its ports — every
# container/network/volume here is prefixed with a fresh per-run id and is
# torn down at the end, pass or fail.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRILLS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ODOO_DIR="${DRILLS_DIR}/odoo"

# shellcheck source=../lib/s3-local.sh
source "${DRILLS_DIR}/lib/s3-local.sh"

PASS=0
FAIL=0
FAILURES=()

log() { echo "[test] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
assert() {
    local desc="$1" ok="$2"
    if [ "$ok" = "true" ]; then
        PASS=$((PASS + 1))
        log "PASS: ${desc}"
    else
        FAIL=$((FAIL + 1))
        FAILURES+=("$desc")
        log "FAIL: ${desc}"
    fi
}

# ---------------------------------------------------------------------------
# Docker availability
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "SKIP: Docker is not available in this environment — nothing to run."
    exit 0
fi

# ---------------------------------------------------------------------------
# Odoo image discovery (local only — never pulls, never touches the shared
# kodemeio-odoo dev stack's containers).
# ---------------------------------------------------------------------------
ODOO_IMAGE="${DRILL_TEST_ODOO_IMAGE:-}"
if [ -z "$ODOO_IMAGE" ]; then
    ODOO_IMAGE="$(docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -iE 'odoo' | grep -viE 'backup|filestore' | head -1)"
fi
if [ -z "$ODOO_IMAGE" ]; then
    echo "SKIP: no local Odoo image found (docker images | grep -i odoo). Set DRILL_TEST_ODOO_IMAGE to override."
    exit 0
fi
log "using Odoo image: ${ODOO_IMAGE}"

WORKDIR="$(mktemp -d)"
TESTID="drilltest-$(date +%s)-$$"
S3_NAME="${TESTID}-s3local"
S3_NET="${TESTID}-s3net"
S3_DATA="${WORKDIR}/s3data"
S3_ACCESS="drilltestkey"
S3_SECRET="drilltestsecret12345"

CLEANED_UP=false
cleanup() {
    $CLEANED_UP && return 0
    CLEANED_UP=true
    log "cleaning up test fixtures (${TESTID})"
    s3_local_stop "$S3_NAME" "$S3_NET"
    # Some containers (postgres, restic, rclone) write as root inside
    # $WORKDIR; a plain host `rm -rf` can leave root-owned debris behind.
    docker run --rm -v "${WORKDIR}:/work" python:3.12-slim rm -rf /work >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Local S3 stand-in
# ---------------------------------------------------------------------------
log "starting local S3 stand-in"
s3_local_start "$S3_NAME" "$S3_NET" "$S3_DATA" "$S3_ACCESS" "$S3_SECRET"
mkdir -p "${S3_DATA}/pgdump" "${S3_DATA}/filestore"
s3_local_wait_ready "$S3_NET" "$S3_ACCESS" "$S3_SECRET" "$S3_NAME" \
    || { echo "FATAL: local S3 stand-in never became ready"; exit 1; }

# Env drill-odoo.sh / make-fixture.sh both read (real drills point these at
# B2; here they point at the stand-in).
export RESTIC_PASSWORD="drilltest-restic-pass"
export AWS_ACCESS_KEY_ID="$S3_ACCESS"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET"
export RCLONE_CONFIG_SRC_TYPE=s3
export RCLONE_CONFIG_SRC_PROVIDER=Other
export RCLONE_CONFIG_SRC_ENV_AUTH=false
export RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$S3_ACCESS"
export RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$S3_SECRET"
export RCLONE_CONFIG_SRC_ENDPOINT="http://${S3_NAME}:9000"

# DRILL_FETCH_EXTRA_CONNECT (test-only hook in drill-odoo.sh): attach the
# stand-in to the drill's own fetch network as soon as it's created, with
# no name-discovery race.
export DRILL_FETCH_EXTRA_CONNECT="$S3_NAME"

run_drill() {
    local project="$1" db_name="$2" dump_arg="$3" restic_repo="$4" snapshot="$5" out="$6"
    # ATTACHMENTS_RES_MODEL scopes validate_odoo.py's attachments_ok count
    # to the sale order the fixture created, so it asserts exactly 3 —
    # the whole restored DB also carries the default company logo as a
    # legitimate 4th store_fname attachment, which a real drill's
    # exhaustive filestore check correctly counts but this fixture-specific
    # assertion does not care about.
    DRILL_PROJECT="$project" ATTACHMENTS_RES_MODEL="sale.order" \
        bash "${ODOO_DIR}/drill-odoo.sh" \
        --db-name "$db_name" --dump "$dump_arg" --restic-repo "$restic_repo" \
        --snapshot "$snapshot" --odoo-image "$ODOO_IMAGE" --sample 20 --out "$out"
}

# ---------------------------------------------------------------------------
# W6: positive end-to-end drill
# ---------------------------------------------------------------------------
log "=== W6: positive drill ==="
POS_PROJECT="${TESTID}-pos"
POS_META="${WORKDIR}/pos-meta.env"
bash "${ODOO_DIR}/make-fixture.sh" \
    --odoo-image "$ODOO_IMAGE" --project "$POS_PROJECT" \
    --s3-network "$S3_NET" --s3-host "$S3_NAME" \
    --s3-access-key "$S3_ACCESS" --s3-secret-key "$S3_SECRET" \
    --db-name drill_src --pg-prefix drill_src --fs-prefix drill_src \
    --meta-out "$POS_META"
FIXTURE_RC=$?
assert "positive fixture build succeeded" "$([ "$FIXTURE_RC" -eq 0 ] && echo true || echo false)"

POS_OUT="${WORKDIR}/results-pos.json"
if [ "$FIXTURE_RC" -eq 0 ]; then
    # shellcheck disable=SC1090
    source "$POS_META"

    run_drill "${POS_PROJECT}-drill" "$FIXTURE_DB" "src:${DUMP_PATH}" "$RESTIC_REPO" "$RESTIC_SNAPSHOT" "$POS_OUT"
    DRILL_RC=$?
    assert "positive drill exits 0" "$([ "$DRILL_RC" -eq 0 ] && echo true || echo false)"

    if [ -s "$POS_OUT" ]; then
        STATUS="$(python3 -c "import json;print(json.load(open('${POS_OUT}'))['status'])" 2>/dev/null || echo "")"
        assert "results.json status == ok" "$([ "$STATUS" = "ok" ] && echo true || echo false)"

        STEPS_HAVE_SECONDS="$(python3 -c "
import json
r = json.load(open('${POS_OUT}'))
steps = r.get('steps', [])
print('true' if steps and all('seconds' in s for s in steps) else 'false')
" 2>/dev/null || echo false)"
        assert "every step has a 'seconds' timing" "$STEPS_HAVE_SECONDS"

        RPO_OK="$(python3 -c "
import json
r = json.load(open('${POS_OUT}'))
v = r.get('rpo_seconds')
print('true' if isinstance(v, int) and v >= 0 else 'false')
" 2>/dev/null || echo false)"
        assert "rpo_seconds >= 0" "$RPO_OK"

        ATTACH_OK="$(python3 -c "
import json
r = json.load(open('${POS_OUT}'))
print('true' if r.get('validation', {}).get('attachments_ok') == 3 else 'false')
" 2>/dev/null || echo false)"
        assert "validation.attachments_ok == 3" "$ATTACH_OK"

        EGRESS_OK="$(python3 -c "
import json
r = json.load(open('${POS_OUT}'))
print('true' if r.get('validation', {}).get('egress_blocked') is True else 'false')
" 2>/dev/null || echo false)"
        assert "validation.egress_blocked == true" "$EGRESS_OK"
    else
        assert "results.json status == ok" false
        assert "every step has a 'seconds' timing" false
        assert "rpo_seconds >= 0" false
        assert "validation.attachments_ok == 3" false
        assert "validation.egress_blocked == true" false
    fi
fi

# ---------------------------------------------------------------------------
# W7a: negative — a missing attachment blob
# ---------------------------------------------------------------------------
log "=== W7a: negative drill (missing attachment) ==="
NEG_PROJECT="${TESTID}-neg1"
NEG_META="${WORKDIR}/neg1-meta.env"
bash "${ODOO_DIR}/make-fixture.sh" \
    --odoo-image "$ODOO_IMAGE" --project "$NEG_PROJECT" \
    --s3-network "$S3_NET" --s3-host "$S3_NAME" \
    --s3-access-key "$S3_ACCESS" --s3-secret-key "$S3_SECRET" \
    --db-name drill_src_neg1 --pg-prefix drill_src_neg1 --fs-prefix drill_src_neg1 \
    --drop-attachment-index 0 \
    --meta-out "$NEG_META"
NEG_FIXTURE_RC=$?
assert "negative (missing attachment) fixture build succeeded" "$([ "$NEG_FIXTURE_RC" -eq 0 ] && echo true || echo false)"

NEG_OUT="${WORKDIR}/results-neg1.json"
if [ "$NEG_FIXTURE_RC" -eq 0 ]; then
    # shellcheck disable=SC1090
    source "$NEG_META"

    run_drill "${NEG_PROJECT}-drill" "$FIXTURE_DB" "src:${DUMP_PATH}" "$RESTIC_REPO" "$RESTIC_SNAPSHOT" "$NEG_OUT"
    NEG_DRILL_RC=$?
    assert "negative drill exits non-zero" "$([ "$NEG_DRILL_RC" -ne 0 ] && echo true || echo false)"

    if [ -s "$NEG_OUT" ]; then
        NAMES_ID="$(python3 -c "
import json
r = json.load(open('${NEG_OUT}'))
missing = r.get('validation', {}).get('filestore', {}).get('missing_ids', [])
print('true' if ${DROPPED_ATTACHMENT_ID:-0} in missing else 'false')
" 2>/dev/null || echo false)"
        assert "results.json names the missing attachment id" "$NAMES_ID"
    else
        assert "results.json names the missing attachment id" false
    fi
fi

# ---------------------------------------------------------------------------
# W7b: negative — pairing warning (dump newer than filestore snapshot)
# ---------------------------------------------------------------------------
log "=== W7b: negative drill (pairing warning) ==="
PAIR_PROJECT="${TESTID}-neg2"
PAIR_META="${WORKDIR}/neg2-meta.env"
bash "${ODOO_DIR}/make-fixture.sh" \
    --odoo-image "$ODOO_IMAGE" --project "$PAIR_PROJECT" \
    --s3-network "$S3_NET" --s3-host "$S3_NAME" \
    --s3-access-key "$S3_ACCESS" --s3-secret-key "$S3_SECRET" \
    --db-name drill_src_neg2 --pg-prefix drill_src_neg2 --fs-prefix drill_src_neg2 \
    --meta-out "$PAIR_META"
PAIR_FIXTURE_RC=$?
assert "pairing-warning fixture build succeeded" "$([ "$PAIR_FIXTURE_RC" -eq 0 ] && echo true || echo false)"

PAIR_OUT="${WORKDIR}/results-neg2.json"
if [ "$PAIR_FIXTURE_RC" -eq 0 ]; then
    # shellcheck disable=SC1090
    source "$PAIR_META"

    # Re-upload the same dump bytes under a timestamp 1h after the
    # filestore snapshot, so drill-odoo.sh's own pairing check (dump newer
    # than the filestore it is paired with) fires.
    LATER_STAMP="$(python3 -c "
import datetime
t = datetime.datetime.strptime('${SNAPSHOT_TIME}', '%Y-%m-%dT%H:%M:%SZ') + datetime.timedelta(hours=1)
print(t.strftime('%Y%m%dT%H%M%SZ'))
")"
    LATER_DUMP_PATH="pgdump/drill_src_neg2/${LATER_STAMP}.sql.gz"
    docker run --rm --network "$S3_NET" \
        -e RCLONE_CONFIG_S3LOCAL_TYPE=s3 -e RCLONE_CONFIG_S3LOCAL_PROVIDER=Other \
        -e RCLONE_CONFIG_S3LOCAL_ENV_AUTH=false \
        -e RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID="$S3_ACCESS" \
        -e RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY="$S3_SECRET" \
        -e RCLONE_CONFIG_S3LOCAL_ENDPOINT="http://${S3_NAME}:9000" \
        "$S3_LOCAL_IMAGE" copyto "s3local:${DUMP_PATH}" "s3local:${LATER_DUMP_PATH}"

    run_drill "${PAIR_PROJECT}-drill" "$FIXTURE_DB" "src:${LATER_DUMP_PATH}" "$RESTIC_REPO" "$RESTIC_SNAPSHOT" "$PAIR_OUT"

    if [ -s "$PAIR_OUT" ]; then
        HAS_WARNING="$(python3 -c "
import json
r = json.load(open('${PAIR_OUT}'))
warnings = r.get('warnings', [])
print('true' if any('pairing' in w and 'older than dump' in w for w in warnings) else 'false')
" 2>/dev/null || echo false)"
        assert "results.json warnings contains 'pairing: filestore older than dump'" "$HAS_WARNING"
    else
        assert "results.json warnings contains 'pairing: filestore older than dump'" false
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=================================================================="
echo "test_drill_odoo.sh: ${PASS} passed, ${FAIL} failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failures:"
    for f in "${FAILURES[@]}"; do echo "  - $f"; done
fi
echo "=================================================================="

[ "$FAIL" -eq 0 ]
