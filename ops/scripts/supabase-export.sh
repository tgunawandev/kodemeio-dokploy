#!/usr/bin/env bash
# supabase-export.sh — founder-run offsite export of a Supabase project
# (Wave 0 spec D17, runbook ops/runbooks/supabase-export.md).
#
# Usage:
#   supabase-export.sh --project-ref REF [--name NAME] [--out-remote REMOTE]
#                      [--storage-endpoint URL] [--storage-region REGION]
#                      [--work-dir DIR] [--execute]
#
# DRY RUN BY DEFAULT: prints the plan and calls nothing. --execute runs it.
#
# Steps (--execute):
#   1. supabase db dump --role-only   -> roles.sql
#   2. supabase db dump               -> schema.sql
#   3. supabase db dump --data-only --use-copy -> data.sql
#   4. rclone copy sb: <work>/storage/        (every Storage bucket, S3 API)
#   5. manifest.json (every file: path, bytes, sha256)
#   6. rclone copy <work> <out-remote>; rclone check --one-way <work> <out-remote>
#
# Environment (founder supplies at run time, from 1Password — never committed):
#   SUPABASE_DB_URL            postgres connection string (percent-encoded). NEVER
#                              printed: every line of output is filtered and the
#                              URL / its password are replaced with ****.
#   RCLONE_CONFIG_SB_ACCESS_KEY_ID / RCLONE_CONFIG_SB_SECRET_ACCESS_KEY
#                              Storage S3 access keys (Dashboard -> Storage -> S3)
#   RCLONE_CONFIG_B2_ACCOUNT / RCLONE_CONFIG_B2_KEY (or any remote named in
#                              --out-remote)  B2 key WITHOUT deleteFiles
# Defaults: --name = --project-ref; --out-remote =
#   b2:kod-prod-backup/supabase/<name>/<UTCSTAMP>/ ;
#   --storage-endpoint = https://<ref>.supabase.co/storage/v1/s3
#
# Exit: 0 ok, 1 a step failed (nothing uploaded unless every dump succeeded),
# 2 usage / missing environment.
set -uo pipefail

PROJECT_REF=""
NAME=""
OUT_REMOTE=""
STORAGE_ENDPOINT=""
STORAGE_REGION="${RCLONE_CONFIG_SB_REGION:-}"
WORK_DIR=""
EXECUTE=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

usage() { sed -n '2,33p' "$0" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --project-ref) [ $# -ge 2 ] || usage; PROJECT_REF="$2"; shift 2 ;;
        --name) [ $# -ge 2 ] || usage; NAME="$2"; shift 2 ;;
        --out-remote) [ $# -ge 2 ] || usage; OUT_REMOTE="$2"; shift 2 ;;
        --storage-endpoint) [ $# -ge 2 ] || usage; STORAGE_ENDPOINT="$2"; shift 2 ;;
        --storage-region) [ $# -ge 2 ] || usage; STORAGE_REGION="$2"; shift 2 ;;
        --work-dir) [ $# -ge 2 ] || usage; WORK_DIR="$2"; shift 2 ;;
        --execute) EXECUTE=1; shift ;;
        --dry-run) EXECUTE=0; shift ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

[ -n "$PROJECT_REF" ] || { echo "--project-ref is required" >&2; usage; }
case "$PROJECT_REF$NAME" in *[!A-Za-z0-9_-]*) echo "--project-ref/--name: letters, digits, - and _ only" >&2; exit 2 ;; esac
NAME="${NAME:-$PROJECT_REF}"
OUT_REMOTE="${OUT_REMOTE:-b2:kod-prod-backup/supabase/${NAME}/${STAMP}/}"
STORAGE_ENDPOINT="${STORAGE_ENDPOINT:-https://${PROJECT_REF}.supabase.co/storage/v1/s3}"
WORK_DIR="${WORK_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/supabase-export-${NAME}-XXXXXX")}"

DB_URL="${SUPABASE_DB_URL:-}"
DB_PASS=""
if [ -n "$DB_URL" ]; then
    # password = between the first ':' after '//' and the last '@' of userinfo
    _rest="${DB_URL#*://}"
    _userinfo="${_rest%@*}"
    [ "$_userinfo" != "$_rest" ] && case "$_userinfo" in *:*) DB_PASS="${_userinfo#*:}" ;; esac
fi

masked_url() {
    [ -n "$DB_URL" ] || { printf '(unset)'; return; }
    local scheme="${DB_URL%%://*}" rest="${DB_URL#*://}"
    local host="${rest##*@}" user="${rest%@*}"
    user="${user%%:*}"
    printf '%s://%s:****@%s' "$scheme" "$user" "$host"
}

# Filter every line of child output: the URL and the password become ****.
mask() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$DB_URL" ] && line="${line//"$DB_URL"/****}"
        [ -n "$DB_PASS" ] && line="${line//"$DB_PASS"/****}"
        printf '%s\n' "$line"
    done
}

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | mask; }

plan() {
    log "PLAN project-ref=$PROJECT_REF name=$NAME mode=$([ "$EXECUTE" = 1 ] && echo EXECUTE || echo DRY-RUN)"
    log "PLAN db-url=$(masked_url)"
    log "PLAN 1 supabase db dump --role-only             -> $WORK_DIR/roles.sql"
    log "PLAN 2 supabase db dump                         -> $WORK_DIR/schema.sql"
    log "PLAN 3 supabase db dump --data-only --use-copy  -> $WORK_DIR/data.sql"
    log "PLAN 4 rclone copy sb: (endpoint $STORAGE_ENDPOINT) -> $WORK_DIR/storage/"
    log "PLAN 5 manifest.json (path, bytes, sha256)"
    log "PLAN 6 rclone copy + check --one-way -> $OUT_REMOTE"
}

plan
if [ "$EXECUTE" != 1 ]; then
    log "DRY-RUN: nothing executed. Re-run with --execute."
    exit 0
fi

[ -n "$DB_URL" ] || { echo "SUPABASE_DB_URL is not set" >&2; exit 2; }
for v in RCLONE_CONFIG_SB_ACCESS_KEY_ID RCLONE_CONFIG_SB_SECRET_ACCESS_KEY; do
    [ -n "${!v:-}" ] || { echo "$v is not set" >&2; exit 2; }
done
command -v supabase >/dev/null || { echo "supabase CLI not found" >&2; exit 2; }
command -v rclone >/dev/null || { echo "rclone not found" >&2; exit 2; }

export RCLONE_CONFIG_SB_TYPE=s3
export RCLONE_CONFIG_SB_PROVIDER=Other
export RCLONE_CONFIG_SB_ENDPOINT="$STORAGE_ENDPOINT"
[ -n "$STORAGE_REGION" ] && export RCLONE_CONFIG_SB_REGION="$STORAGE_REGION"

mkdir -p "$WORK_DIR/storage"

run() { # label, command...
    local label="$1"; shift
    log "STEP $label"
    # stdout+stderr both go through the mask; PIPESTATUS keeps the real exit code
    "$@" 2>&1 | mask
    local rc="${PIPESTATUS[0]}"
    if [ "$rc" -ne 0 ]; then
        log "FAILED $label (exit $rc) -- nothing uploaded; work dir kept: $WORK_DIR"
        exit 1
    fi
}

# NOTE: the CLI takes the URL only as an argument (--db-url), so it is visible
# in the local process table while a dump runs — run this on a single-user
# machine (runbook). It never reaches this script's output.
run roles  supabase db dump --db-url "$DB_URL" --role-only -f "$WORK_DIR/roles.sql"
run schema supabase db dump --db-url "$DB_URL" -f "$WORK_DIR/schema.sql"
run data   supabase db dump --db-url "$DB_URL" --data-only --use-copy -f "$WORK_DIR/data.sql"
run storage rclone copy sb: "$WORK_DIR/storage"

log "STEP manifest"
if ! python3 - "$WORK_DIR" "$PROJECT_REF" "$NAME" "$STAMP" <<'PY'
import hashlib, json, os, sys
root, ref, name, stamp = sys.argv[1:5]
files = []
for d, _, fs in os.walk(root):
    for f in sorted(fs):
        p = os.path.join(d, f)
        rel = os.path.relpath(p, root)
        if rel == "manifest.json":
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        files.append({"path": rel, "bytes": os.path.getsize(p), "sha256": h.hexdigest()})
files.sort(key=lambda x: x["path"])
for required in ("roles.sql", "schema.sql", "data.sql"):
    if not any(f["path"] == required and f["bytes"] > 0 for f in files):
        sys.exit(f"missing or empty {required}")
json.dump({"project_ref": ref, "name": name, "stamp": stamp, "files": files},
          open(os.path.join(root, "manifest.json"), "w"), indent=2)
print(f"manifest files={len(files)}")
PY
then
    log "FAILED manifest -- nothing uploaded; work dir kept: $WORK_DIR"
    exit 1
fi

run upload rclone copy "$WORK_DIR" "$OUT_REMOTE"
run verify rclone check --one-way "$WORK_DIR" "$OUT_REMOTE"
log "RESULT=ok out=$OUT_REMOTE work=$WORK_DIR"
