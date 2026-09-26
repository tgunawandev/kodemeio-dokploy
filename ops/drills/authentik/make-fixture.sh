#!/usr/bin/env bash
# make-fixture.sh — synthetic Authentik backup for the local drill test (W8).
#
# Boots a FRESH, throwaway Authentik (compose.drill.yml, its own project and
# internal network), adds one known user and one known media file, then
# produces the two backup artefacts exactly as production lays them out and
# uploads them to the local S3 stand-in (lib/s3-local.sh), under the kod
# offsite mirror's paths (kod-offsite-mirror.sh: b2:kod-prod-backup/<hz bucket>/...):
#
#   kod-prod-backup/kodemeio-postgres-backup/authentik/<ISO-ts>.sql.gz
#       pg_dump -Fc | gzip   (kodemeio-postgres scripts/s3.sh — Authentik's
#                             live DB is on kod postgres, Task 0 H2)
#   kod-prod-backup/kodemeio-authentik-backup/media/authentik-media-<UTCSTAMP>.tar.gz
#       tar -C /data -czf .  (kodemeio-authentik backup/backup.sh, Task 6)
#
# Usage:
#   make-fixture.sh --s3-net NET --s3-host HOST --s3-access KEY --s3-secret SECRET
#                   --out-env FILE
# Writes FILE with FIXTURE_USERS (user count in the dumped DB),
# FIXTURE_MEDIA_FILES, FIXTURE_DUMP_KEY, FIXTURE_MEDIA_KEY. Tears its own
# Authentik down on exit, pass or fail.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.drill.yml"
# shellcheck source=../lib/s3-local.sh
source "${SCRIPT_DIR}/../lib/s3-local.sh"

S3_NET="" S3_HOST="" S3_ACCESS="" S3_SECRET="" OUT_ENV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --s3-net) S3_NET="$2"; shift 2 ;;
        --s3-host) S3_HOST="$2"; shift 2 ;;
        --s3-access) S3_ACCESS="$2"; shift 2 ;;
        --s3-secret) S3_SECRET="$2"; shift 2 ;;
        --out-env) OUT_ENV="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
for v in S3_NET S3_HOST S3_ACCESS S3_SECRET OUT_ENV; do
    [ -n "${!v}" ] || { echo "--$(echo "$v" | tr 'A-Z_' 'a-z-') is required" >&2; exit 2; }
done

log() { echo "[ak-fixture] $(date -u +%H:%M:%S) $*" >&2; }
die() { log "FATAL: $*"; exit 1; }

AUTHENTIK_IMAGE="${AUTHENTIK_IMAGE:-ghcr.io/goauthentik/server:2026.2.3}"
PROJECT_NAME="akfixture-$(date +%s)-$$"
export PROJECT_NAME AUTHENTIK_IMAGE
DRILL_PG_PASSWORD="$(openssl rand -hex 16)"
AUTHENTIK_SECRET_KEY="$(openssl rand -hex 32)"
export DRILL_PG_PASSWORD AUTHENTIK_SECRET_KEY
WORK="$(mktemp -d)"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE")

cleanup() {
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    docker run --rm -v "${WORK}:/w" alpine:3.20 rm -rf /w/* >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

log "booting a fresh Authentik (${PROJECT_NAME})"
# Populate the shared media volume serially BEFORE Compose runs: server and
# worker mount ${PROJECT_NAME}-media and Compose creates them concurrently, so
# both would try to copy the image's /data (with its `media -> /media` symlink)
# into the same empty volume and one creation dies with
#   failed to create symlink: …/_data/media: file exists
# See the same helper in drill-authentik.sh — measured on 2026-09-26.
docker volume create "${PROJECT_NAME}-media" >/dev/null 2>&1 || die "cannot create the media volume"
docker run --rm --entrypoint /bin/sh -v "${PROJECT_NAME}-media:/data" \
    "$AUTHENTIK_IMAGE" -c 'true' >/dev/null 2>&1 \
    || die "cannot pre-populate the media volume with ${AUTHENTIK_IMAGE}"
"${COMPOSE[@]}" up -d >"${WORK}/up.log" 2>&1 || die "compose up failed: $(tail -3 "${WORK}/up.log")"
ready=""
for _ in $(seq 1 "${AK_BOOT_TRIES:-180}"); do
    ready="$(docker run --rm --network "${PROJECT_NAME}-net" python:3.12-alpine python -c \
        "import urllib.request as u; print(u.urlopen('http://server:9000/-/health/ready/', timeout=5).status)" 2>/dev/null)"
    [ "$ready" = 200 ] && break
    sleep 4
done
[ "$ready" = 200 ] || die "fixture Authentik never became ready"

log "adding the known user + media file"
out="$("${COMPOSE[@]}" exec -T server ak shell -c "
from authentik.core.models import User
u, _ = User.objects.get_or_create(username='fixture_person', defaults={'name': 'Fixture Person', 'email': 'fixture@example.invalid'})
print('FIXTURE_USERS', User.objects.count())
" </dev/null 2>/dev/null)"
users="$(sed -n 's/^FIXTURE_USERS \([0-9][0-9]*\)$/\1/p' <<< "$out" | tail -1)"
[ -n "$users" ] && [ "$users" -ge 2 ] || die "could not create the fixture user (got '${users}')"
"${COMPOSE[@]}" exec -T server sh -c \
    'mkdir -p /data/fixture && printf "drill-fixture-media\n" > /data/fixture/icon.txt && printf "second\n" > /data/fixture/bg.txt' </dev/null \
    || die "could not write media fixture"

TS="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_KEY="kodemeio-postgres-backup/authentik/${TS}.sql.gz"
MEDIA_KEY="kodemeio-authentik-backup/media/authentik-media-${STAMP}.tar.gz"

log "pg_dump -Fc | gzip  (kodemeio-postgres layout)"
"${COMPOSE[@]}" exec -T postgresql sh -c 'pg_dump -U authentik -d authentik -Fc | gzip' </dev/null > "${WORK}/dump.sql.gz" \
    || die "pg_dump failed"
[ -s "${WORK}/dump.sql.gz" ] || die "empty dump"

log "tar -C /data -czf .  (Task 6 sidecar layout)"
docker run --rm -v "${PROJECT_NAME}-media:/data:ro" -v "${WORK}:/out" alpine:3.20 \
    sh -c 'tar -C /data -czf /out/media.tar.gz . && tar -tzf /out/media.tar.gz | grep -vc "/$"' > "${WORK}/media.count" \
    || die "media tar failed"
media_files="$(tr -d '[:space:]' < "${WORK}/media.count")"

log "uploading to the S3 stand-in"
s3_local_rclone "$S3_NET" "$S3_ACCESS" "$S3_SECRET" "$S3_HOST" mkdir "s3local:kod-prod-backup" >/dev/null 2>&1 || true
upload() { # local file in WORK, key under kod-prod-backup (the rclone image's entrypoint IS rclone)
    # Credentials by NAME, never `-e VAR=value`: the latter puts the secret in
    # the docker CLI's argv, which `ps` and /proc expose to every local user.
    RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID="$S3_ACCESS" \
    RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY="$S3_SECRET" \
        docker run --rm --network "$S3_NET" -v "${WORK}:/work:ro" \
        -e RCLONE_CONFIG_S3LOCAL_TYPE=s3 -e RCLONE_CONFIG_S3LOCAL_PROVIDER=Other \
        -e RCLONE_CONFIG_S3LOCAL_ENV_AUTH=false \
        -e RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID -e RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY \
        -e RCLONE_CONFIG_S3LOCAL_ENDPOINT="http://${S3_HOST}:9000" \
        "$S3_LOCAL_IMAGE" copyto "/work/$1" "s3local:kod-prod-backup/$2" >"${WORK}/upload.log" 2>&1
}
upload dump.sql.gz "$DUMP_KEY" || die "dump upload failed: $(tail -2 "${WORK}/upload.log")"
upload media.tar.gz "$MEDIA_KEY" || die "media upload failed: $(tail -2 "${WORK}/upload.log")"

{
    echo "FIXTURE_USERS=${users}"
    echo "FIXTURE_MEDIA_FILES=${media_files}"
    echo "FIXTURE_DUMP_KEY=${DUMP_KEY}"
    echo "FIXTURE_MEDIA_KEY=${MEDIA_KEY}"
} > "$OUT_ENV"
log "fixture ready: users=${users} media_files=${media_files}"
