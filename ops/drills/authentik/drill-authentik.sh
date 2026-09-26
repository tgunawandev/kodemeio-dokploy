#!/usr/bin/env bash
# drill-authentik.sh — timed, isolated Authentik restore drill with app-level
# validation (Teracorp Wave 0 Task 8, spec D11–D13).
#
# Usage:
#   drill-authentik.sh --dump <remote:path|remote:prefix/latest> \
#       --media <remote:path|remote:prefix/latest> \
#       [--image ghcr.io/goauthentik/server:2026.2.3] [--flow default-authentication-flow] \
#       [--docker-host ssh://...] [--keep] --out <results.json>
#
#   Real drill (from the B2 offsite copy):
#     --dump  src:kod-prod-backup/kodemeio-postgres-backup/authentik/latest
#     --media src:kod-prod-backup/kodemeio-authentik-backup/media/latest
#
# Env:
#   RCLONE_CONFIG_SRC_*   the source remote in rclone env-config form (B2
#                         read-only key in a real drill; the S3 stand-in in tests)
#   AUTHENTIK_SECRET_KEY  optional. Default: a fresh random key — Authentik uses
#                         the key only for cookie signing (ops/wave0/facts.md),
#                         and the drill proving login with a NEW key is the point.
#
# Steps (timed via lib/timing.sh): fetch_dump, fetch_media, restore_db,
# restore_media, boot, set_drill_password, validate. Every resource is named
# from a fresh per-run project id; the Authentik stack is on an internal
# network; the worker has no docker.sock; nothing publishes a port; all is
# torn down at the end unless --keep. Results JSON is written even on failure
# (status=failed + failed_step).
#
# restore_db decompresses the dump to a file once, validates it as an archive
# (`pg_restore --list`) and then restores WITHOUT --exit-on-error: a kod
# production dump is taken from the kodemeio-postgres image and names
# extensions a vanilla postgres:16-alpine cannot apply, so a few error lines
# are expected. The step is judged by the user count read back from SQL and by
# nothing else; the error count is recorded in the results as `restore_errors`.
# The dump is fed by redirection, never through a pipe — see the comment at the
# --list check for why a pipe fails a valid archive.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.drill.yml"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.75.1}"
PY_IMAGE="${PY_IMAGE:-python:3.12-alpine}"

log() { echo "[drill-authentik] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }
die() { log "FATAL: $*"; exit 2; }

DUMP_ARG="" MEDIA_ARG="" OUT="" DOCKER_HOST_ARG="" KEEP=false
AUTHENTIK_IMAGE="${AUTHENTIK_IMAGE:-ghcr.io/goauthentik/server:2026.2.3}"
FLOW="default-authentication-flow"
while [ $# -gt 0 ]; do
    case "$1" in
        --dump) DUMP_ARG="$2"; shift 2 ;;
        --media) MEDIA_ARG="$2"; shift 2 ;;
        --image) AUTHENTIK_IMAGE="$2"; shift 2 ;;
        --flow) FLOW="$2"; shift 2 ;;
        --docker-host) DOCKER_HOST_ARG="$2"; shift 2 ;;
        --keep) KEEP=true; shift ;;
        --out) OUT="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[ -n "$DUMP_ARG" ] || die "--dump is required"
[ -n "$MEDIA_ARG" ] || die "--media is required"
[ -n "$OUT" ] || die "--out is required"
[ -n "${RCLONE_CONFIG_SRC_TYPE:-}" ] || die "RCLONE_CONFIG_SRC_* (the source remote) must be set"
[ -n "$DOCKER_HOST_ARG" ] && export DOCKER_HOST="$DOCKER_HOST_ARG"
export AUTHENTIK_IMAGE

PROJECT="${DRILL_PROJECT:-akdrill-$(date +%s)-$$}"
export PROJECT_NAME="$PROJECT"
FETCHNET="${PROJECT}-fetchnet"
WORKDIR="$(mktemp -d)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_EPOCH="$(date -u +%s)"
SECRET_KEY_SOURCE="$([ -n "${AUTHENTIK_SECRET_KEY:-}" ] && echo provided || echo random)"
DRILL_PG_PASSWORD="$(openssl rand -hex 16)"
AUTHENTIK_SECRET_KEY="${AUTHENTIK_SECRET_KEY:-$(openssl rand -hex 32)}"
DRILL_PASS="$(openssl rand -hex 20)"   # never echoed, logged or written out
export DRILL_PG_PASSWORD AUTHENTIK_SECRET_KEY

export TIMING_FILE="${WORKDIR}/timings.jsonl"
# shellcheck source=../lib/timing.sh
source "${SCRIPT_DIR}/../lib/timing.sh"

COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE")
STATUS=ok FAILED_STEP="" FAILED_DETAIL="" EXPECTED_USERS="" ACTUAL_USERS="" VALIDATE_JSON="{}"
DUMP_KEY="" MEDIA_KEY="" DUMP_TIME="" MEDIA_TIME="" MEDIA_FILES="" RESTORE_ERRORS=""

CLEANED=false
cleanup() {
    $CLEANED && return 0
    CLEANED=true
    if $KEEP; then
        log "--keep: leaving project ${PROJECT} up (docker compose -p ${PROJECT} -f ${COMPOSE_FILE} down -v to remove)"
    else
        "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if [ -n "${DRILL_FETCH_EXTRA_CONNECT:-}" ]; then
        docker network disconnect -f "$FETCHNET" "$DRILL_FETCH_EXTRA_CONNECT" >/dev/null 2>&1 || true
    fi
    docker network rm "$FETCHNET" >/dev/null 2>&1 || true
    docker run --rm -v "${WORKDIR}:/w" alpine:3.20 sh -c 'rm -rf /w/*' >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

fail_step() {
    STATUS=failed; FAILED_STEP="$1"; FAILED_DETAIL="${2:-}"
    t_end "$1" failed
    log "step $1 FAILED${2:+: $2}"
}

# pass the source remote's settings by NAME only (-e VAR), so no key value
# ever appears in a docker command line
src_env() { env | grep -oE '^RCLONE_CONFIG_SRC_[A-Z0-9_]+' | sed 's/^/-e\n/'; }
rclone_src() { # rclone args... (on the fetch network, WORKDIR at /work)
    local flags=()
    mapfile -t flags < <(src_env)
    docker run --rm --network "$FETCHNET" -v "${WORKDIR}:/work" "${flags[@]}" "$RCLONE_IMAGE" "$@"
}
resolve_latest() { # remote:prefix/latest | remote:path -> remote:path
    local arg="$1" prefix newest
    if [ "$(basename "$arg")" != latest ]; then echo "$arg"; return 0; fi
    prefix="$(dirname "$arg")"
    newest="$(rclone_src lsf "${prefix}/" --files-only 2>/dev/null | sort | tail -1)"
    [ -n "$newest" ] || return 1
    echo "${prefix}/${newest}"
}
ts_epoch() { # parse the timestamp embedded in a backup key -> epoch (empty if none)
    python3 - "$1" <<'PY'
import re, sys, datetime as d
k = sys.argv[1].rsplit("/", 1)[-1]
m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", k)
if m:
    t = d.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
else:
    m = re.search(r"(\d{8}T\d{6})Z", k)
    t = d.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S") if m else None
print(int(t.replace(tzinfo=d.timezone.utc).timestamp()) if t else "")
PY
}

docker network create "$FETCHNET" >/dev/null || die "cannot create ${FETCHNET}"
[ -n "${DRILL_FETCH_EXTRA_CONNECT:-}" ] && docker network connect "$FETCHNET" "$DRILL_FETCH_EXTRA_CONNECT" 2>/dev/null

# --- fetch -------------------------------------------------------------------
t_start fetch_dump
if DUMP_KEY="$(resolve_latest "$DUMP_ARG")" && rclone_src copyto "$DUMP_KEY" /work/dump.sql.gz >/dev/null 2>&1 \
    && [ -s "${WORKDIR}/dump.sql.gz" ]; then
    DUMP_TIME="$(ts_epoch "$DUMP_KEY")"; t_end fetch_dump ok
else
    fail_step fetch_dump "could not fetch ${DUMP_ARG}"
fi

if [ "$STATUS" = ok ]; then
    t_start fetch_media
    if MEDIA_KEY="$(resolve_latest "$MEDIA_ARG")" && rclone_src copyto "$MEDIA_KEY" /work/media.tar.gz >/dev/null 2>&1 \
        && [ -s "${WORKDIR}/media.tar.gz" ]; then
        MEDIA_TIME="$(ts_epoch "$MEDIA_KEY")"; t_end fetch_media ok
    else
        fail_step fetch_media "could not fetch ${MEDIA_ARG}"
    fi
fi

# --- restore -----------------------------------------------------------------
# Reported per sub-step on purpose: "restore_db failed" alone cannot tell a
# broken compose project from a corrupt dump, and a drill nobody can debug is
# a drill that stays broken.
tr3() { tail -3 "$1" 2>/dev/null | tr '\n' ' '; }

# populate_media_volume — Docker copies an image's content into a named volume
# the first time a container mounts it, but only while that volume is EMPTY.
# `server` and `worker` share ${PROJECT}-media and Compose creates them
# CONCURRENTLY (no `--parallel` exists on `create` or `up`), so both try to
# populate the same fresh volume and one dies with
#   failed to create symlink: …/_data/media: file exists
# because the image's /data carries a `media -> /media` symlink. Observed on
# this workstation 2026-09-26, after four clean runs — i.e. a race, not a
# constant. Doing the population once, serially, before Compose runs makes the
# race impossible and leaves the volume's content identical to a real
# deployment's (the image's own /data, symlink included).
populate_media_volume() {
    docker volume create "${PROJECT}-media" >/dev/null 2>&1 || return 1
    docker run --rm --entrypoint /bin/sh -v "${PROJECT}-media:/data" \
        "$AUTHENTIK_IMAGE" -c 'true' >/dev/null 2>&1
}

if [ "$STATUS" = ok ]; then
    t_start restore_db
    if ! populate_media_volume; then
        fail_step restore_db "could not pre-populate the ${PROJECT}-media volume with ${AUTHENTIK_IMAGE}"
    elif ! "${COMPOSE[@]}" create >"${WORKDIR}/create.log" 2>&1; then
        fail_step restore_db "compose create failed: $(tr3 "${WORKDIR}/create.log")"
    elif ! "${COMPOSE[@]}" up -d --wait postgresql >"${WORKDIR}/pguplog" 2>&1; then
        fail_step restore_db "postgresql never became healthy: $(tr3 "${WORKDIR}/pguplog")"
    elif ! gunzip -c "${WORKDIR}/dump.sql.gz" >"${WORKDIR}/dump.sql" 2>"${WORKDIR}/gunzip.log"; then
        fail_step restore_db "gunzip failed: $(tr3 "${WORKDIR}/gunzip.log")"
    elif ! "${COMPOSE[@]}" exec -T postgresql pg_restore --list -U authentik \
            <"${WORKDIR}/dump.sql" >"${WORKDIR}/list.log" 2>&1; then
        # A garbage or truncated dump is rejected HERE, before the DB is
        # touched — pg_restore: "input file does not appear to be a valid
        # archive". Nothing reads the source dump's format more authoritatively.
        #
        # The dump is decompressed to a file and fed by REDIRECT, never by a
        # pipe: the custom format carries its TOC near the start, so `--list`
        # stops reading as soon as it has it, and a piped `gunzip` then dies
        # of SIGPIPE (141) — which `set -o pipefail` reports as a failed
        # check on a perfectly good archive. Measured, not theorised.
        fail_step restore_db "not a valid pg_restore archive: $(tr3 "${WORKDIR}/list.log")"
    else
        # NO --exit-on-error: a production dump is taken from the
        # kodemeio-postgres image (pgaudit, plus extension comments a plain
        # postgres cannot apply) and those statements legitimately error on
        # the vanilla postgres:16-alpine target. The restore is judged by what
        # is actually IN the database afterwards, not by pg_restore's exit
        # status; the error lines are counted and reported, never hidden.
        "${COMPOSE[@]}" exec -T postgresql pg_restore --no-owner --no-acl \
            -U authentik -d authentik <"${WORKDIR}/dump.sql" >"${WORKDIR}/restore.log" 2>&1 || true
        RESTORE_ERRORS="$(grep -c '^pg_restore: error:' "${WORKDIR}/restore.log" 2>/dev/null || true)"
        EXPECTED_USERS="$("${COMPOSE[@]}" exec -T postgresql psql -U authentik -d authentik -tAc \
            'SELECT count(*) FROM authentik_core_user' </dev/null 2>/dev/null | tr -d '[:space:]')"
        if [[ "$EXPECTED_USERS" =~ ^[0-9]+$ ]] && [ "$EXPECTED_USERS" -gt 0 ]; then
            log "restore: ${EXPECTED_USERS} users in the restored DB, ${RESTORE_ERRORS:-0} pg_restore error line(s)"
            [ "${RESTORE_ERRORS:-0}" -gt 0 ] && log "first restore error: $(grep -m1 '^pg_restore: error:' "${WORKDIR}/restore.log" 2>/dev/null)"
            t_end restore_db ok
        else
            fail_step restore_db "restored DB has no readable authentik_core_user (pg_restore errors: ${RESTORE_ERRORS:-0}; $(tr3 "${WORKDIR}/restore.log"))"
        fi
    fi
fi

if [ "$STATUS" = ok ]; then
    t_start restore_media
    # `! -type d`, matching make-fixture.sh's count exactly: `tar -tzf | grep
    # -vc "/$"` counts every NON-directory entry, and a real Authentik /data
    # (this fixture included) carries a `media` symlink beside the files.
    # `find -type f` silently drops that symlink and the count then never
    # reconciles — a false failure that says nothing about the restore.
    MEDIA_FILES="$(docker run --rm -v "${PROJECT}-media:/data" -v "${WORKDIR}:/in:ro" alpine:3.20 sh -c '
        want=$(tar -tzf /in/media.tar.gz | grep -vc "/$") &&
        tar -xzf /in/media.tar.gz -C /data &&
        have=$(find /data ! -type d | wc -l) &&
        [ "$have" -ge "$want" ] && echo "$want"' 2>/dev/null)"
    if [[ "$MEDIA_FILES" =~ ^[0-9]+$ ]]; then t_end restore_media ok
    else fail_step restore_media "media tar did not restore completely"; fi
fi

# --- boot + validate -----------------------------------------------------------
if [ "$STATUS" = ok ]; then
    t_start boot
    ready=""
    if "${COMPOSE[@]}" up -d server worker >/dev/null 2>&1; then
        for _ in $(seq 1 "${AK_BOOT_TRIES:-180}"); do  # x4 s; first boot runs migrations
            ready="$(docker run --rm --network "${PROJECT}-net" "$PY_IMAGE" python -c \
                "import urllib.request as u; print(u.urlopen('http://server:9000/-/health/ready/', timeout=5).status)" 2>/dev/null)"
            [ "$ready" = 200 ] && break
            sleep 4
        done
    fi
    if [ "$ready" = 200 ]; then t_end boot ok; else fail_step boot "never answered /-/health/ready/"; fi
fi

if [ "$STATUS" = ok ]; then
    t_start set_drill_password
    out="$(DRILL_PASS="$DRILL_PASS" "${COMPOSE[@]}" exec -T -e DRILL_PASS server ak shell -c "
import os
from authentik.core.models import User, Group
print('USER_COUNT', User.objects.exclude(username='drill_validator').count())
u, _ = User.objects.get_or_create(username='drill_validator', defaults={'name': 'Restore drill validator'})
u.set_password(os.environ['DRILL_PASS']); u.is_active = True; u.save()
Group.objects.get(name='authentik Admins').users.add(u)
print('DRILL_USER_OK')
" </dev/null 2>/dev/null)"
    ACTUAL_USERS="$(sed -n 's/^USER_COUNT \([0-9][0-9]*\)$/\1/p' <<< "$out" | tail -1)"
    if grep -qx DRILL_USER_OK <<< "$out" && [ -n "$ACTUAL_USERS" ]; then t_end set_drill_password ok
    else fail_step set_drill_password "ak shell could not create the drill user"; fi
fi

if [ "$STATUS" = ok ]; then
    t_start validate
    VALIDATE_JSON="$(DRILL_PASS="$DRILL_PASS" docker run --rm --network "${PROJECT}-net" -e DRILL_PASS \
        -v "${SCRIPT_DIR}/validate_authentik.py:/v.py:ro" "$PY_IMAGE" python /v.py \
        --flow "$FLOW" --expected-users "$EXPECTED_USERS" --actual-users "$ACTUAL_USERS" 2>/dev/null)"
    vrc=$?
    # shellcheck disable=SC2089,SC2090  # a JSON string, not shell words
    python3 -c 'import json,sys; json.loads(sys.argv[1])' "$VALIDATE_JSON" 2>/dev/null || VALIDATE_JSON='{"ok": false, "error": "validator produced no JSON"}'
    if [ "$vrc" -eq 0 ]; then t_end validate ok; else fail_step validate "$(python3 -c '
import json,sys; c=json.loads(sys.argv[1]).get("checks",{}); print(",".join(k for k,v in c.items() if not v.get("ok")) or "validator error")' "$VALIDATE_JSON")"; fi
fi

# --- results -------------------------------------------------------------------
STEPS_JSON="$(t_steps_json)"
mkdir -p "$(dirname "$OUT")"
# shellcheck disable=SC2090
export STEPS_JSON VALIDATE_JSON STARTED_AT STARTED_EPOCH STATUS FAILED_STEP FAILED_DETAIL AUTHENTIK_IMAGE FLOW \
    SECRET_KEY_SOURCE DUMP_KEY MEDIA_KEY DUMP_TIME MEDIA_TIME MEDIA_FILES EXPECTED_USERS RESTORE_ERRORS
python3 - "$OUT" <<'PY'
import hashlib, json, os, sys, datetime as d
e = os.environ
steps = json.loads(e["STEPS_JSON"])
val = json.loads(e["VALIDATE_JSON"])
num = lambda k: int(e[k]) if e.get(k) else None
times = [int(e[k]) for k in ("DUMP_TIME", "MEDIA_TIME") if e.get(k)]
res = {
    "target": "authentik",
    "started_at": e["STARTED_AT"],
    "finished_at": d.datetime.now(d.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status": e["STATUS"],
    "failed_step": e.get("FAILED_STEP") or None,
    # Why the step failed, verbatim — a results file that says only
    # failed_step=restore_db cannot tell a bad dump from a bad compose project.
    "failed_detail": e.get("FAILED_DETAIL") or None,
    "image": e["AUTHENTIK_IMAGE"],
    "flow": e["FLOW"],
    "secret_key": e["SECRET_KEY_SOURCE"],
    "dump_key": e.get("DUMP_KEY") or None,
    "media_key": e.get("MEDIA_KEY") or None,
    "media_files": num("MEDIA_FILES"),
    "expected_users": num("EXPECTED_USERS"),
    # pg_restore error lines that did NOT stop the restore (a production dump
    # names extensions the vanilla target image lacks). Reported, not hidden:
    # a real drill should show the operator exactly what was skipped.
    "restore_errors": num("RESTORE_ERRORS"),
    # RPO = age of the OLDER of the two artefacts at drill start (worst case)
    "rpo_seconds": (int(e["STARTED_EPOCH"]) - min(times)) if len(times) == 2 else None,
    "rto_seconds": round(sum(s.get("seconds", 0) for s in steps), 3),
    "steps": steps,
    "validation": val,
    "validator_sha256": hashlib.sha256(json.dumps(val, sort_keys=True).encode()).hexdigest(),
}
json.dump(res, open(sys.argv[1], "w"), indent=2, sort_keys=True)
print(json.dumps(res, indent=2, sort_keys=True))
PY
log "results written to ${OUT} (status=${STATUS}${FAILED_STEP:+, failed_step=${FAILED_STEP}})"
[ "$STATUS" = ok ]
