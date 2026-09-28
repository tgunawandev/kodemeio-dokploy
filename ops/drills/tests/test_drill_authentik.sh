#!/usr/bin/env bash
# test_drill_authentik.sh — local end-to-end test of the Authentik restore
# drill (Wave 0 Task 8, spec W8).
#
# Positive: make-fixture.sh boots a fresh Authentik, adds a known user and
# media files, dumps DB + tars media into a local S3 stand-in in the kod
# offsite layout; drill-authentik.sh restores the `latest` of each into an
# isolated stack with a DIFFERENT random secret key and must report
# status == ok: ready, user count equal to the fixture's, drill-user login
# through the flow executor, wrong password refused, egress blocked, every
# step timed, rpo_seconds >= 0.
# Negative: a corrupted dump uploaded as the newest object -> status failed,
# failed_step restore_db, non-zero exit, results JSON still written.
#
# Skips (exit 0) without Docker. Every container/network/volume is per-run
# and removed at the end, pass or fail.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRILLS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AK_DIR="${DRILLS_DIR}/authentik"
# shellcheck source=../lib/s3-local.sh
source "${DRILLS_DIR}/lib/s3-local.sh"

PASS=0 FAIL=0
ok()   { PASS=$((PASS + 1)); echo "ok   - $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "FAIL - $1"; }
check() { if [ "$2" = true ]; then ok "$1"; else bad "$1"; fi; }

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "SKIP: Docker is not available"; exit 0
fi

TESTID="akdrilltest-$(date +%s)-$$"
WORK="$(mktemp -d)"
S3_NAME="${TESTID}-s3local" S3_NET="${TESTID}-s3net" S3_DATA="${WORK}/s3data"
S3_ACCESS="akdrillkey" S3_SECRET="akdrillsecret12345"

cleanup() {
    s3_local_stop "$S3_NAME" "$S3_NET"
    docker run --rm -v "${WORK}:/w" alpine:3.20 sh -c 'rm -rf /w/*' >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

s3_local_start "$S3_NAME" "$S3_NET" "$S3_DATA" "$S3_ACCESS" "$S3_SECRET"
mkdir -p "${S3_DATA}/kod-prod-backup"
s3_local_wait_ready "$S3_NET" "$S3_ACCESS" "$S3_SECRET" "$S3_NAME" || { echo "FATAL: S3 stand-in not ready"; exit 1; }

echo "--- fixture ---"
bash "${AK_DIR}/make-fixture.sh" --s3-net "$S3_NET" --s3-host "$S3_NAME" \
    --s3-access "$S3_ACCESS" --s3-secret "$S3_SECRET" --out-env "${WORK}/fixture.env" \
    || { echo "FATAL: make-fixture.sh failed"; exit 1; }
# shellcheck disable=SC1091
source "${WORK}/fixture.env"
check "fixture has users (${FIXTURE_USERS})" "$([ "${FIXTURE_USERS:-0}" -ge 2 ] && echo true || echo false)"
check "fixture has media files (${FIXTURE_MEDIA_FILES})" "$([ "${FIXTURE_MEDIA_FILES:-0}" -ge 2 ] && echo true || echo false)"
check "dump in the kod offsite layout" "$([ -s "${S3_DATA}/kod-prod-backup/${FIXTURE_DUMP_KEY}" ] && echo true || echo false)"
check "media tar in the Task 6 layout" "$([ -s "${S3_DATA}/kod-prod-backup/${FIXTURE_MEDIA_KEY}" ] && echo true || echo false)"

export RCLONE_CONFIG_SRC_TYPE=s3 RCLONE_CONFIG_SRC_PROVIDER=Other RCLONE_CONFIG_SRC_ENV_AUTH=false
export RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$S3_ACCESS" RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$S3_SECRET"
export RCLONE_CONFIG_SRC_ENDPOINT="http://${S3_NAME}:9000"
export DRILL_FETCH_EXTRA_CONNECT="$S3_NAME"
unset AUTHENTIK_SECRET_KEY   # the drill must work with a fresh random key

# --- results-JSON readers ---------------------------------------------------
# Every reader is a FIXED program: the caller passes a key path, never source.
# The previous `eval()` helper built Python out of fixture values, so a quote in
# a value would have become code — data must not become source in a file the
# founder runs. Values are resolved here and compared in SHELL instead.
#
# jq is deliberately not used: the drill's validator image (python:3.12-alpine)
# has none (verified), and this test already needs python3.

# jg FILE [KEY…] — the value at that path, or nothing when the path is absent
# or null. A missing path prints nothing on purpose: the check that wanted it
# then fails rather than crashing.
jg() { python3 -c '
import json, sys
try:
    node = json.load(open(sys.argv[1]))
    for key in sys.argv[2:]:
        node = node[int(key)] if key.lstrip("-").isdigit() else node[key]
except Exception:
    node = None
print("" if node is None else node)
' "$@"; }

# jg_true FILE [KEY…] — "true" only for the JSON boolean true (not 1, not "true")
jg_true() { python3 -c '
import json, sys
try:
    node = json.load(open(sys.argv[1]))
    for key in sys.argv[2:]:
        node = node[int(key)] if key.lstrip("-").isdigit() else node[key]
except Exception:
    node = None
print("true" if node is True else "false")
' "$@"; }

# jg_type FILE [KEY…] — the value's Python type name: int, float, str, bool,
# list, dict, NoneType. Exact, so a bool is not accepted where an int is wanted.
jg_type() { python3 -c '
import json, sys
try:
    node = json.load(open(sys.argv[1]))
    for key in sys.argv[2:]:
        node = node[int(key)] if key.lstrip("-").isdigit() else node[key]
except Exception:
    node = None
print(type(node).__name__)
' "$@"; }

# The two facts the "all 7 steps" check needs, as fixed programs rather than one
# clever expression built from them.
jg_step_names() { python3 -c '
import json, sys
print(" ".join(s.get("name", "") for s in json.load(open(sys.argv[1])).get("steps", [])))
' "$1"; }
jg_steps_all_ok() { python3 -c '
import json, sys
steps = json.load(open(sys.argv[1])).get("steps", [])
print("true" if steps and all(s.get("status") == "ok" for s in steps) else "false")
' "$1"; }

EXPECTED_STEPS="fetch_dump fetch_media restore_db restore_media boot set_drill_password validate"

# matches PATTERN VALUE — a substring test written once, so the call sites below
# read as comparisons.
matches() { case "$2" in *"$1"*) echo true ;; *) echo false ;; esac; }
num_ge() { awk -v v="$1" -v n="$2" 'BEGIN { exit !(v + 0 >= n + 0) }' && echo true || echo false; }
num_gt() { awk -v v="$1" -v n="$2" 'BEGIN { exit !(v + 0 > n + 0) }' && echo true || echo false; }

echo "--- positive: restore latest ---"
bash "${AK_DIR}/drill-authentik.sh" \
    --dump src:kod-prod-backup/kodemeio-postgres-backup/authentik/latest \
    --media src:kod-prod-backup/kodemeio-authentik-backup/media/latest \
    --out "${WORK}/ok.json" > "${WORK}/ok.log" 2>&1
rc=$?
check "drill exit 0" "$([ "$rc" = 0 ] && echo true || echo false)"
if [ -s "${WORK}/ok.json" ]; then
    R="${WORK}/ok.json"
    check "status == ok" "$([ "$(jg "$R" status)" = ok ] && echo true || echo false)"
    check "resolved latest dump key" "$(matches "${FIXTURE_DUMP_KEY}" "$(jg "$R" dump_key)")"
    check "user count equals fixture (${FIXTURE_USERS})" \
        "$([ "$(jg "$R" validation checks user_count actual)" = "${FIXTURE_USERS}" ] \
            && [ "$(jg "$R" expected_users)" = "${FIXTURE_USERS}" ] && echo true || echo false)"
    check "drill user logged in via flow executor" "$(jg_true "$R" validation checks login ok)"
    check "login trail ends in xak-flow-redirect" \
        "$(matches xak-flow-redirect "$(jg "$R" validation checks login trail)")"
    check "wrong password refused" "$(jg_true "$R" validation checks wrong_password ok)"
    check "egress blocked" "$(jg_true "$R" validation checks egress_blocked ok)"
    check "media restored (${FIXTURE_MEDIA_FILES} files)" \
        "$([ "$(jg "$R" media_files)" = "${FIXTURE_MEDIA_FILES}" ] && echo true || echo false)"
    check "all 7 steps timed ok" \
        "$([ "$(jg_step_names "$R")" = "$EXPECTED_STEPS" ] \
            && [ "$(jg_steps_all_ok "$R")" = true ] && echo true || echo false)"
    check "rpo_seconds >= 0 and rto > 0" \
        "$([ "$(jg_type "$R" rpo_seconds)" = int ] && [ "$(jg_type "$R" rto_seconds)" = float ] \
            && [ "$(num_ge "$(jg "$R" rpo_seconds)" 0)" = true ] \
            && [ "$(num_gt "$(jg "$R" rto_seconds)" 0)" = true ] && echo true || echo false)"
    check "fresh random secret key used" "$([ "$(jg "$R" secret_key)" = random ] && echo true || echo false)"
    check "restore error count recorded" \
        "$([ "$(jg_type "$R" restore_errors)" = int ] \
            && [ "$(num_ge "$(jg "$R" restore_errors)" 0)" = true ] && echo true || echo false)"
else
    bad "results JSON missing"
fi
# The drill's own log is the evidence, pass or fail: on failure the per-step
# message names the sub-step (compose create / postgresql / archive / restore)
# and the tail of the artefact that failed; on success the timing lines and the
# restored row counts are what a results table is built from. Hiding them on
# success is how a green run stops being evidence.
echo "--- drill-authentik.sh: step timings + restored counts ---"
grep -E '^\[timing\]|^\[drill-authentik\] (restore|results)' "${WORK}/ok.log" 2>/dev/null || true
[ "$rc" != 0 ] && { echo "--- drill-authentik.sh output (tail) ---"; tail -40 "${WORK}/ok.log"; }
true

echo "--- negative: corrupt newest dump ---"
later="$(date -u -d '+1 hour' +%Y-%m-%dT%H:%M:%S.000Z)"
# Uploaded through the S3 stand-in, never written by the host: rclone inside
# the stand-in container owns the bucket directories (it runs as root), so a
# host-side redirect into them is a permission error, not a test.
# Credentials by NAME, never `-e VAR=value` (argv is world-readable).
printf 'not a pg dump' | gzip | \
    RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID="$S3_ACCESS" \
    RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY="$S3_SECRET" \
    docker run --rm -i --network "$S3_NET" \
    -e RCLONE_CONFIG_S3LOCAL_TYPE=s3 -e RCLONE_CONFIG_S3LOCAL_PROVIDER=Other \
    -e RCLONE_CONFIG_S3LOCAL_ENV_AUTH=false \
    -e RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID \
    -e RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY \
    -e RCLONE_CONFIG_S3LOCAL_ENDPOINT="http://${S3_NAME}:9000" \
    "$S3_LOCAL_IMAGE" rcat "s3local:kod-prod-backup/kodemeio-postgres-backup/authentik/${later}.sql.gz" \
    || { bad "could not upload the corrupt dump"; }
bash "${AK_DIR}/drill-authentik.sh" \
    --dump src:kod-prod-backup/kodemeio-postgres-backup/authentik/latest \
    --media src:kod-prod-backup/kodemeio-authentik-backup/media/latest \
    --out "${WORK}/bad.json" > "${WORK}/bad.log" 2>&1
rc=$?
check "corrupt dump -> non-zero exit" "$([ "$rc" != 0 ] && echo true || echo false)"
if [ -s "${WORK}/bad.json" ]; then
    check "corrupt dump -> status failed at restore_db" \
        "$([ "$(jg "${WORK}/bad.json" status)" = failed ] \
            && [ "$(jg "${WORK}/bad.json" failed_step)" = restore_db ] && echo true || echo false)"
    check "corrupt dump rejected as an archive, not as a DB error" \
        "$(matches "not a valid pg_restore archive" "$(jg "${WORK}/bad.json" failed_detail)")"
else
    bad "results JSON not written on failure"
fi
grep -q "step restore_db FAILED" "${WORK}/bad.log" \
    && ok "negative drill names the failing step in its own log" \
    || bad "negative drill names the failing step in its own log"; tail -20 "${WORK}/bad.log"

echo "--- no leftovers, no secrets ---"
left="$(docker ps -a --format '{{.Names}}' | grep -c 'akdrill-\|akfixture-' || true)"
check "no drill/fixture containers left" "$([ "$left" = 0 ] && echo true || echo false)"
vleft="$(docker volume ls --format '{{.Name}}' | grep -c 'akdrill-\|akfixture-' || true)"
check "no drill/fixture volumes left" "$([ "$vleft" = 0 ] && echo true || echo false)"
check "S3 secret never in drill output" "$(grep -q "$S3_SECRET" "${WORK}/ok.log" "${WORK}/ok.json" && echo false || echo true)"

echo
echo "passed=${PASS} failed=${FAIL}"
if [ "$FAIL" -eq 0 ]; then echo "ALL PASS"; else exit 1; fi
