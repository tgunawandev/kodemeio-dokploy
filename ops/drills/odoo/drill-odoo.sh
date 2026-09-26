#!/usr/bin/env bash
# drill-odoo.sh — timed, isolated Odoo DB+filestore restore drill with
# app-level validation (Teracorp Wave 0 Task 7).
#
# Usage:
#   drill-odoo.sh --db-name kod_odoo_erp --dump <remote:path|latest> \
#       --restic-repo <s3:...> --snapshot <id|latest> \
#       --odoo-image <ghcr...:tag> [--docker-host ssh://...] \
#       [--sample 20] [--keep] --out <results.json>
#
# Required env:
#   RESTIC_PASSWORD                  restic repository password
#   AWS_ACCESS_KEY_ID/SECRET          B2 read-only key (or the local S3
#                                     stand-in's key pair, in tests)
#   RCLONE_CONFIG_SRC_*               the source remote for --dump, in
#                                     rclone's env-config form (TYPE,
#                                     PROVIDER, ENV_AUTH, ACCESS_KEY_ID,
#                                     SECRET_ACCESS_KEY, ENDPOINT, ...)
#
# Every resource this script creates is named from $PROJECT (a fresh,
# random project id per run), on its own Docker networks and volumes —
# never the shared kodemeio-odoo dev stack, never its DBs, never its ports.
# Nothing here publishes a host port; the validator reaches Odoo over the
# `drill` network's internal DNS only. Everything is torn down at the end
# unless --keep is given.
#
# Steps (each timed by lib/timing.sh): fetch_dump, restore_db, neutralise,
# restore_filestore, boot_odoo, validate.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.drill.yml"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.75.1}"
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.19.1}"

# shellcheck source=../lib/timing.sh
log() { echo "[drill-odoo] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }
die() {
    log "FATAL: $*"
    exit 1
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
DB_NAME=""
DUMP_ARG=""
RESTIC_REPO=""
SNAPSHOT="latest"
ODOO_IMAGE=""
DOCKER_HOST_ARG=""
SAMPLE=20
KEEP=false
OUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --db-name) DB_NAME="$2"; shift 2 ;;
        --dump) DUMP_ARG="$2"; shift 2 ;;
        --restic-repo) RESTIC_REPO="$2"; shift 2 ;;
        --snapshot) SNAPSHOT="$2"; shift 2 ;;
        --odoo-image) ODOO_IMAGE="$2"; shift 2 ;;
        --docker-host) DOCKER_HOST_ARG="$2"; shift 2 ;;
        --sample) SAMPLE="$2"; shift 2 ;;
        --keep) KEEP=true; shift ;;
        --out) OUT="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$DB_NAME" ] || die "--db-name is required"
[ -n "$DUMP_ARG" ] || die "--dump is required"
[ -n "$RESTIC_REPO" ] || die "--restic-repo is required"
[ -n "$ODOO_IMAGE" ] || die "--odoo-image is required"
[ -n "$OUT" ] || die "--out is required"

: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"

[ -n "$DOCKER_HOST_ARG" ] && export DOCKER_HOST="$DOCKER_HOST_ARG"

# ---------------------------------------------------------------------------
# Isolation: a fresh project id, its own networks, its own volumes.
# ---------------------------------------------------------------------------
# DRILL_PROJECT lets a test harness pin a predictable project id (so it can
# attach its own local S3 stand-in to the fetch network deterministically,
# with no name-discovery race). Unset in every real run, where the random
# id is what guarantees isolation from anything else on the host.
PROJECT="${DRILL_PROJECT:-teradrill-$(date +%s)-$$}"
FETCHNET="${PROJECT}-fetchnet"
WORKDIR="$(mktemp -d)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_EPOCH="$(date -u +%s)"

DRILL_PG_PASSWORD="$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
DRILL_ADMIN_PASSWD="$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
DRILL_PASS="$(openssl rand -hex 20 2>/dev/null || head -c20 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# ^ DRILL_PASS is intentionally never echoed, logged or written to results.json.

TIMING_FILE="${WORKDIR}/timings.jsonl"
export TIMING_FILE
# shellcheck source=../lib/timing.sh
source "${SCRIPT_DIR}/../lib/timing.sh"

STATUS="ok"
FAILED_STEP=""
WARNINGS_JSON="[]"
DUMP_TIME=""
SNAPSHOT_TIME=""
EXPECTED_ORDERS=""
VALIDATE_JSON="{}"

COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE")

CLEANED_UP=false
cleanup() {
    $CLEANED_UP && return 0
    CLEANED_UP=true
    if [ "$KEEP" = "true" ]; then
        log "KEEP: leaving project '${PROJECT}' running — clean up yourself with:"
        log "  docker compose -p ${PROJECT} -f ${COMPOSE_FILE} down -v --remove-orphans"
        log "  docker network rm ${FETCHNET}"
        return 0
    fi
    log "cleaning up project ${PROJECT}"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    # A container joined via DRILL_FETCH_EXTRA_CONNECT is still attached —
    # `docker network rm` refuses a network with a live endpoint on it.
    if [ -n "${DRILL_FETCH_EXTRA_CONNECT:-}" ]; then
        for extra_container in $DRILL_FETCH_EXTRA_CONNECT; do
            docker network disconnect -f "$FETCHNET" "$extra_container" >/dev/null 2>&1 || true
        done
    fi
    docker network rm "$FETCHNET" >/dev/null 2>&1 || true
    # Some containers (postgres, restic, rclone) write as root inside
    # $WORKDIR; a plain host `rm -rf` can leave root-owned debris behind.
    docker run --rm -v "${WORKDIR}:/work" python:3.12-slim rm -rf /work >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

fail_step() {
    local step="$1"
    STATUS="failed"
    FAILED_STEP="$step"
    log "STEP FAILED: ${step}"
}

# ---------------------------------------------------------------------------
# Resolve the repo's own uv venv site-packages, so the validator container
# gets httpx without any pip install (the drill network has no egress).
# ---------------------------------------------------------------------------
resolve_venv_site_packages() {
    local venv_dir="${REPO_ROOT}/.venv"
    if [ ! -d "$venv_dir" ]; then
        log "no .venv in ${REPO_ROOT} — running 'uv sync' (needed for httpx)"
        (cd "$REPO_ROOT" && uv sync) || die "uv sync failed"
    fi
    local sp
    sp="$(find "${venv_dir}/lib" -maxdepth 1 -type d -name 'python3.*' 2>/dev/null | sort | tail -1)"
    [ -n "$sp" ] || die "could not find python3.* under ${venv_dir}/lib"
    echo "${sp}/site-packages"
}
VENV_SITE_PACKAGES="$(resolve_venv_site_packages)"

build_odoo_env_args() {
    local pgdatabase="$1" init_db="$2"
    ODOO_ENV_ARGS=(
        -e PGHOST=db -e PGPORT=5432 -e PGUSER=odoo -e PGPASSWORD="$DRILL_PG_PASSWORD" -e PGDATABASE="$pgdatabase"
        -e ODOO_DATA_DIR=/var/lib/odoo
        -e ODOO_DB_FILTER="^${pgdatabase}\$"
        -e ODOO_LIST_DB=False
        -e ODOO_DB_MAXCONN=16
        -e ODOO_HTTP_PORT=8069 -e ODOO_GEVENT_PORT=8072 -e ODOO_PROXY_MODE=False
        -e ODOO_WORKERS=0 -e ODOO_MAX_CRON_THREADS=0
        -e ODOO_ADMIN_PASSWD="$DRILL_ADMIN_PASSWD"
        -e ODOO_LOG_LEVEL=warn
        -e ODOO_LIMIT_TIME_CPU=600 -e ODOO_LIMIT_TIME_REAL=1200 -e ODOO_LIMIT_TIME_REAL_CRON=1800
        -e ODOO_LIMIT_MEMORY_SOFT=2147483648 -e ODOO_LIMIT_MEMORY_HARD=4294967296
        -e RUNNING_ENV=drill -e WITHOUT_DEMO=True
        -e ODOO_SERVER_WIDE_MODULES=base,web
        -e ODOO_SKIP_MANDATORY_MODULES=true
        -e ODOO_INIT_DB="$init_db" -e ODOO_INIT_MODULES=base
    )
}

# Forward every currently-exported RCLONE_CONFIG_SRC_* variable into a
# `docker run` (bare `-e NAME` inherits the value from this shell's env).
rclone_src_env_flags() {
    local name
    for name in $(compgen -v | grep '^RCLONE_CONFIG_SRC_' || true); do
        printf -- '-e\n%s\n' "$name"
    done
}

# resolve_dump_path <remote:path> — if the last path segment is literally
# "latest", list the parent prefix and pick the lexicographically newest
# object (our object names are ISO8601 stamps, so this sorts correctly).
resolve_dump_path() {
    local dump="$1" remote path prefix newest
    remote="${dump%%:*}"
    path="${dump#*:}"
    if [ "$(basename "$path")" != "latest" ]; then
        echo "$dump"
        return 0
    fi
    prefix="$(dirname "$path")"
    mapfile -t RCLONE_FLAGS < <(rclone_src_env_flags)
    newest="$(docker run --rm --network "$FETCHNET" "${RCLONE_FLAGS[@]}" \
        "$RCLONE_IMAGE" lsf "${remote}:${prefix}/" --files-only 2>/dev/null | sort | tail -1)"
    [ -n "$newest" ] || die "no objects found under ${remote}:${prefix}/ to resolve 'latest'"
    echo "${remote}:${prefix}/${newest}"
}

extract_iso_stamp() {
    # Pulls a YYYYMMDDTHHMMSSZ token out of a filename and renders it as
    # YYYY-MM-DDTHH:MM:SSZ. Prints nothing if no such token is found.
    local name="$1" stamp
    stamp="$(echo "$name" | grep -oE '[0-9]{8}T[0-9]{6}Z' | head -1)"
    [ -n "$stamp" ] || return 0
    echo "${stamp:0:4}-${stamp:4:2}-${stamp:6:2}T${stamp:9:2}:${stamp:11:2}:${stamp:13:2}Z"
}

iso_to_epoch() {
    local iso="$1"
    [ -n "$iso" ] || { echo ""; return 0; }
    date -u -d "$iso" +%s 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------
docker network create "$FETCHNET" >/dev/null

# DRILL_FETCH_EXTRA_CONNECT: space-separated container names a test harness
# wants attached to the fetch network (its local S3 stand-in). Never set in
# a real run — the fetch network otherwise only carries the fetch/restic
# one-shot containers this script itself starts.
if [ -n "${DRILL_FETCH_EXTRA_CONNECT:-}" ]; then
    for extra_container in $DRILL_FETCH_EXTRA_CONNECT; do
        docker network connect "$FETCHNET" "$extra_container" 2>/dev/null || true
    done
fi

export PROJECT_NAME="$PROJECT"
export ODOO_IMAGE
export DRILL_DB_NAME="$DB_NAME"
export DRILL_PG_PASSWORD
export DRILL_ADMIN_PASSWD
export VENV_SITE_PACKAGES
export ODOO_INIT_DB=false
export ODOO_INIT_MODULES=base

# ---------------------------------------------------------------------------
# Step: fetch_dump
# ---------------------------------------------------------------------------
t_start fetch_dump
RESOLVED_DUMP="$(resolve_dump_path "$DUMP_ARG")"
DUMP_TIME="$(extract_iso_stamp "$RESOLVED_DUMP")"
mapfile -t RCLONE_FLAGS < <(rclone_src_env_flags)
if docker run --rm --network "$FETCHNET" -v "${WORKDIR}:/work" "${RCLONE_FLAGS[@]}" \
    "$RCLONE_IMAGE" copyto "$RESOLVED_DUMP" /work/dump.sql.gz; then
    t_end fetch_dump ok
else
    t_end fetch_dump failed
    fail_step fetch_dump
fi

# ---------------------------------------------------------------------------
# Step: restore_db
# ---------------------------------------------------------------------------
if [ "$STATUS" = "ok" ]; then
    t_start restore_db
    "${COMPOSE[@]}" up -d db >/dev/null 2>&1
    DB_CID="$("${COMPOSE[@]}" ps -q db)"
    # The official postgres image briefly accepts connections during its
    # own initdb bootstrap, then restarts for real — `pg_isready` alone can
    # report ready right in that window and a subsequent `createdb` then
    # fails with "connection ... failed: No such file or directory"
    # (verified against a real run, 2026-09-26). Require 3 consecutive real
    # queries to succeed before treating the server as actually up.
    ready=false
    consecutive=0
    for _ in $(seq 1 90); do
        if docker exec "$DB_CID" psql -U odoo -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; then
            consecutive=$((consecutive + 1))
            if [ "$consecutive" -ge 3 ]; then ready=true; break; fi
        else
            consecutive=0
        fi
        sleep 1
    done
    if [ "$ready" != "true" ]; then
        t_end restore_db failed
        fail_step restore_db
    else
        docker cp "${WORKDIR}/dump.sql.gz" "${DB_CID}:/tmp/dump.sql.gz"
        restored=false
        for _ in $(seq 1 3); do
            if docker exec "$DB_CID" sh -c \
                "gunzip -c /tmp/dump.sql.gz > /tmp/dump.dump && createdb -U odoo '${DB_NAME}' && pg_restore -U odoo -d '${DB_NAME}' --no-owner --no-acl /tmp/dump.dump"; then
                restored=true
                break
            fi
            docker exec "$DB_CID" dropdb -U odoo --if-exists "${DB_NAME}" >/dev/null 2>&1 || true
            sleep 2
        done
        if [ "$restored" = "true" ]; then
            EXPECTED_ORDERS="$(docker exec "$DB_CID" psql -U odoo -d "$DB_NAME" -tAc "SELECT count(*) FROM sale_order;" | tr -d ' \r\n')"
            t_end restore_db ok
        else
            t_end restore_db failed
            fail_step restore_db
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Step: neutralise
# ---------------------------------------------------------------------------
if [ "$STATUS" = "ok" ]; then
    t_start neutralise
    read -r -d '' NEUTRALISE_SQL <<'SQL' || true
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='ir_mail_server') THEN
        EXECUTE 'UPDATE ir_mail_server SET active=false';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='fetchmail_server') THEN
        EXECUTE 'UPDATE fetchmail_server SET active=false';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='ir_cron') THEN
        EXECUTE 'UPDATE ir_cron SET active=false';
    END IF;
END $$;
SQL
    if docker exec "$DB_CID" psql -U odoo -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "$NEUTRALISE_SQL"; then
        build_odoo_env_args "$DB_NAME" false
        # `base.group_system` alone (the brief's literal example) is NOT
        # enough for the validator to read sale.order — verified 2026-09-26
        # against a real drill: search_count raised AccessError naming
        # Sales/Administrator, Sales/User, Invoicing and Accounting groups
        # as the ones that actually grant it. Add every group the
        # validator's checks touch, skipping any that don't exist on this
        # target (an hrms-shaped database has no `sale` module installed).
        if docker run --rm -i --network "${PROJECT}-net" \
            -v "${PROJECT}-filestore:/var/lib/odoo" "${ODOO_ENV_ARGS[@]}" \
            -e DRILL_PASS="$DRILL_PASS" \
            "$ODOO_IMAGE" shell <<'PYEOF'
import os
pw = os.environ["DRILL_PASS"]
Users = env["res.users"]
group_xmlids = ("base.group_system", "sale.group_sale_manager", "account.group_account_manager")
group_ids = [g.id for g in (env.ref(x, raise_if_not_found=False) for x in group_xmlids) if g]
u = Users.search([("login", "=", "drill_validator")], limit=1)
if not u:
    u = Users.create({
        "name": "Drill Validator",
        "login": "drill_validator",
        "groups_id": [(6, 0, group_ids)],
    })
else:
    u.write({"groups_id": [(6, 0, group_ids)]})
u.write({"password": pw})
env.cr.commit()
print("DRILL_USER_READY")
PYEOF
        then
            t_end neutralise ok
        else
            t_end neutralise failed
            fail_step neutralise
        fi
    else
        t_end neutralise failed
        fail_step neutralise
    fi
fi

# ---------------------------------------------------------------------------
# Step: restore_filestore
# ---------------------------------------------------------------------------
if [ "$STATUS" = "ok" ]; then
    t_start restore_filestore
    SNAPSHOT_JSON="$(docker run --rm --network "$FETCHNET" \
        -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
        "$RESTIC_IMAGE" -r "$RESTIC_REPO" snapshots --json "$SNAPSHOT" 2>/dev/null)"
    SNAPSHOT_TIME="$(echo "$SNAPSHOT_JSON" | python3 -c "
import json, sys
try:
    rows = json.load(sys.stdin)
    print((rows[-1].get('time') or '')[:19] + 'Z')
except Exception:
    print('')
" 2>/dev/null)"
    if docker run --rm --network "$FETCHNET" -v "${PROJECT}-filestore:/var/lib/odoo" \
        -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
        "$RESTIC_IMAGE" -r "$RESTIC_REPO" restore "$SNAPSHOT" --target /; then
        t_end restore_filestore ok
    else
        t_end restore_filestore failed
        fail_step restore_filestore
    fi
fi

# ---------------------------------------------------------------------------
# Step: boot_odoo (compose resolves db -> odoo-init -> odoo automatically)
# ---------------------------------------------------------------------------
if [ "$STATUS" = "ok" ]; then
    t_start boot_odoo
    if "${COMPOSE[@]}" up -d odoo; then
        ODOO_CID="$("${COMPOSE[@]}" ps -q odoo)"
        healthy=false
        for _ in $(seq 1 180); do # 180*5s = 900s
            hstatus="$(docker inspect --format='{{.State.Health.Status}}' "$ODOO_CID" 2>/dev/null || echo "")"
            [ "$hstatus" = "healthy" ] && { healthy=true; break; }
            [ "$hstatus" = "unhealthy" ] && break
            sleep 5
        done
        if [ "$healthy" = "true" ]; then
            t_end boot_odoo ok
        else
            t_end boot_odoo failed
            fail_step boot_odoo
        fi
    else
        t_end boot_odoo failed
        fail_step boot_odoo
    fi
fi

# ---------------------------------------------------------------------------
# Step: validate
# ---------------------------------------------------------------------------
if [ "$STATUS" = "ok" ]; then
    t_start validate
    # ATTACHMENTS_RES_MODEL is a test-only scoping hook (unset in a real
    # drill) — forwarded only when the caller (test_drill_odoo.sh) set it.
    VALIDATE_EXTRA_FLAGS=()
    [ -n "${ATTACHMENTS_RES_MODEL:-}" ] && VALIDATE_EXTRA_FLAGS+=(-e ATTACHMENTS_RES_MODEL)
    VALIDATE_JSON="$("${COMPOSE[@]}" run --rm \
        -e ODOO_DB="$DB_NAME" -e DRILL_USER=drill_validator -e DRILL_PASS="$DRILL_PASS" \
        -e EXPECTED_ORDERS="$EXPECTED_ORDERS" -e SAMPLE="$SAMPLE" \
        "${VALIDATE_EXTRA_FLAGS[@]}" \
        validator 2>/dev/null)"
    VALIDATE_EXIT=$?
    if [ "$VALIDATE_EXIT" -eq 0 ]; then
        t_end validate ok
    else
        t_end validate failed
        fail_step validate
    fi
fi

# ---------------------------------------------------------------------------
# Pairing warning: filestore snapshot older than the DB dump.
# ---------------------------------------------------------------------------
DUMP_EPOCH="$(iso_to_epoch "$DUMP_TIME")"
SNAPSHOT_EPOCH="$(iso_to_epoch "$SNAPSHOT_TIME")"
if [ -n "$DUMP_EPOCH" ] && [ -n "$SNAPSHOT_EPOCH" ] && [ "$SNAPSHOT_EPOCH" -lt "$DUMP_EPOCH" ]; then
    WARNINGS_JSON='["pairing: filestore older than dump"]'
fi

# ---------------------------------------------------------------------------
# Assemble results.json
# ---------------------------------------------------------------------------
NEWEST_BACKUP_EPOCH="$DUMP_EPOCH"
if [ -n "$SNAPSHOT_EPOCH" ] && { [ -z "$NEWEST_BACKUP_EPOCH" ] || [ "$SNAPSHOT_EPOCH" -gt "$NEWEST_BACKUP_EPOCH" ]; }; then
    NEWEST_BACKUP_EPOCH="$SNAPSHOT_EPOCH"
fi
if [ -n "$NEWEST_BACKUP_EPOCH" ]; then
    RPO_SECONDS=$((STARTED_EPOCH - NEWEST_BACKUP_EPOCH))
else
    RPO_SECONDS=""
fi

STEPS_JSON="$(t_steps_json)"
[ -n "$VALIDATE_JSON" ] || VALIDATE_JSON="{}"
echo "$VALIDATE_JSON" | python3 -c "import json,sys; json.loads(sys.stdin.read() or '{}')" >/dev/null 2>&1 \
    || VALIDATE_JSON='{"error":"validator produced no parsable JSON"}'

# Every dynamic value crosses into Python as an environment variable, never
# by interpolating it into the script text — some of it (validator output,
# an object key resolved from a real backup) is not fully under our
# control, and a stray `$`, quote or `'''` inside it must never corrupt the
# generated program.
STEPS_JSON="$STEPS_JSON" \
VALIDATE_JSON="$VALIDATE_JSON" \
WARNINGS_JSON="$WARNINGS_JSON" \
DRILL_TARGET_DB="$DB_NAME" \
DRILL_STARTED_AT="$STARTED_AT" \
DRILL_DUMP_KEY="$RESOLVED_DUMP" \
DRILL_DUMP_TIME="$DUMP_TIME" \
DRILL_SNAPSHOT_ID="$SNAPSHOT" \
DRILL_SNAPSHOT_TIME="$SNAPSHOT_TIME" \
DRILL_RPO_SECONDS="$RPO_SECONDS" \
DRILL_STATUS="$STATUS" \
DRILL_FAILED_STEP="$FAILED_STEP" \
DRILL_OUT="$OUT" \
python3 <<'PY'
import json
import os

steps = json.loads(os.environ["STEPS_JSON"])
validation = json.loads(os.environ["VALIDATE_JSON"])
warnings = json.loads(os.environ["WARNINGS_JSON"])
rto_seconds = round(sum(s.get("seconds", 0) for s in steps), 3)

rpo_raw = os.environ.get("DRILL_RPO_SECONDS", "")
rpo_seconds = int(rpo_raw) if rpo_raw != "" else None

result = {
    "drill": "odoo",
    "target_db": os.environ["DRILL_TARGET_DB"],
    "started_at": os.environ["DRILL_STARTED_AT"],
    "backup": {
        "dump_key": os.environ["DRILL_DUMP_KEY"],
        "dump_time": os.environ["DRILL_DUMP_TIME"],
        "snapshot_id": os.environ["DRILL_SNAPSHOT_ID"],
        "snapshot_time": os.environ["DRILL_SNAPSHOT_TIME"],
    },
    "rpo_seconds": rpo_seconds,
    "steps": steps,
    "rto_seconds": rto_seconds,
    "validation": validation,
    "status": os.environ["DRILL_STATUS"],
}
failed_step = os.environ.get("DRILL_FAILED_STEP", "")
if failed_step:
    result["failed_step"] = failed_step
if warnings:
    result["warnings"] = warnings

out_path = os.environ["DRILL_OUT"]
with open(out_path, "w") as fh:
    json.dump(result, fh, indent=2, sort_keys=True)
print(json.dumps(result, indent=2, sort_keys=True))
PY

log "results written to ${OUT} (status=${STATUS})"
[ "$STATUS" = "ok" ]
