#!/usr/bin/env bash
# make-fixture.sh — LOCAL ONLY. Builds a synthetic Odoo backup pair (a
# pg_dump + a restic filestore snapshot) that tests/test_drill_odoo.sh feeds
# into drill-odoo.sh, so the restore drill can be exercised end-to-end
# without ever touching a real kod database or bucket.
#
# What it does:
#   1. Boots a throwaway Odoo (`sale_management` only, no mandatory bundle)
#      against a throwaway Postgres, both on their own Docker network.
#   2. Bootstraps the admin user's password via `odoo shell` (a freshly
#      `-i`'d database has no usable admin password otherwise), then uses
#      real XML-RPC (stdlib xmlrpc.client, no deps) to create a partner, a
#      confirmed sale order and 3 binary attachments with known content —
#      exactly as the brief specifies, so the drill validates the same
#      surface a real restore would.
#   3. Optionally deletes one attachment's blob from the filestore BEFORE
#      the restic backup (--drop-attachment-index), for the W7 negative
#      test: a real drill must catch a backup that is missing a file the
#      database still references.
#   4. pg_dump | gzip -> uploaded to the s3-local stand-in at
#      <pg-bucket>/<pg-prefix>/<UTCSTAMP>.sql.gz
#   5. restic init/backup of the filestore directory -> the s3-local
#      stand-in restic repo s3:http://<s3-host>:9000/<fs-bucket>/<fs-prefix>
#   6. Writes --meta-out as shell-sourceable KEY=VALUE lines describing
#      everything the caller needs to drive drill-odoo.sh and assert on its
#      output (dump path, restic repo, snapshot id, dump/snapshot times,
#      expected order count, the 3 attachments' ids/checksums, which one
#      (if any) was dropped).
#   7. Always cleans up its own containers/network/volume, whether it
#      succeeded or not.
#
# Every container this script starts carries a name and volume prefixed
# with --project, and lives on its own Docker network — never the shared
# kodemeio-odoo dev stack, never its DBs, never its ports (nothing here
# publishes a host port).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/s3-local.sh
source "${SCRIPT_DIR}/../lib/s3-local.sh"

log() { echo "[make-fixture] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }
die() {
    log "FATAL: $*"
    exit 1
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
ODOO_IMAGE=""
PROJECT=""
S3_NETWORK=""
S3_HOST=""
S3_ACCESS_KEY=""
S3_SECRET_KEY=""
DB_NAME="drill_src"
PG_BUCKET="pgdump"
PG_PREFIX="drill_src"
FS_BUCKET="filestore"
FS_PREFIX="drill_src"
DROP_ATTACHMENT_INDEX=""
META_OUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --odoo-image) ODOO_IMAGE="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --s3-network) S3_NETWORK="$2"; shift 2 ;;
        --s3-host) S3_HOST="$2"; shift 2 ;;
        --s3-access-key) S3_ACCESS_KEY="$2"; shift 2 ;;
        --s3-secret-key) S3_SECRET_KEY="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --pg-bucket) PG_BUCKET="$2"; shift 2 ;;
        --pg-prefix) PG_PREFIX="$2"; shift 2 ;;
        --fs-bucket) FS_BUCKET="$2"; shift 2 ;;
        --fs-prefix) FS_PREFIX="$2"; shift 2 ;;
        --drop-attachment-index) DROP_ATTACHMENT_INDEX="$2"; shift 2 ;;
        --meta-out) META_OUT="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$ODOO_IMAGE" ] || die "--odoo-image is required"
[ -n "$PROJECT" ] || die "--project is required"
[ -n "$S3_NETWORK" ] || die "--s3-network is required"
[ -n "$S3_HOST" ] || die "--s3-host is required"
[ -n "$S3_ACCESS_KEY" ] || die "--s3-access-key is required"
[ -n "$S3_SECRET_KEY" ] || die "--s3-secret-key is required"
[ -n "$META_OUT" ] || die "--meta-out is required"

: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"

FIXNET="${PROJECT}-fixnet"
PG_CONTAINER="${PROJECT}-pg"
VOLUME="${PROJECT}-filestore"
WORKDIR="$(mktemp -d)"
FIXTURE_ADMIN_PASS="$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"

cleanup() {
    docker rm -f "${PROJECT}-odoo-init" "${PROJECT}-odoo" "${PROJECT}-odoo-shell" "${PROJECT}-xmlrpc" "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$FIXNET" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    # Some containers (postgres, restic, rclone) write as root inside
    # $WORKDIR; a plain host `rm -rf` can leave root-owned debris behind.
    docker run --rm -v "${WORKDIR}:/work" python:3.12-slim rm -rf /work >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Shared Odoo env (mirrors compose.drill.yml's `odoo-env` anchor)
# ---------------------------------------------------------------------------
build_odoo_env_args() {
    local pgdatabase="$1" init_db="$2" init_modules="$3"
    ODOO_ENV_ARGS=(
        -e PGHOST="$PG_CONTAINER" -e PGPORT=5432 -e PGUSER=odoo -e PGPASSWORD=drillpg -e PGDATABASE="$pgdatabase"
        -e ODOO_DATA_DIR=/var/lib/odoo
        -e ODOO_DB_FILTER="^${pgdatabase}\$"
        -e ODOO_LIST_DB=False
        -e ODOO_DB_MAXCONN=16
        -e ODOO_HTTP_PORT=8069 -e ODOO_GEVENT_PORT=8072 -e ODOO_PROXY_MODE=False
        -e ODOO_WORKERS=0 -e ODOO_MAX_CRON_THREADS=0
        -e ODOO_ADMIN_PASSWD=drillmaster
        -e ODOO_LOG_LEVEL=warn
        -e ODOO_LIMIT_TIME_CPU=600 -e ODOO_LIMIT_TIME_REAL=1200 -e ODOO_LIMIT_TIME_REAL_CRON=1800
        -e ODOO_LIMIT_MEMORY_SOFT=2147483648 -e ODOO_LIMIT_MEMORY_HARD=4294967296
        -e RUNNING_ENV=drill -e WITHOUT_DEMO=True
        -e ODOO_SERVER_WIDE_MODULES=base,web
        -e ODOO_SKIP_MANDATORY_MODULES=true
        -e ODOO_INIT_DB="$init_db" -e ODOO_INIT_MODULES="$init_modules"
    )
}

# ---------------------------------------------------------------------------
# 1. Throwaway Postgres + Odoo
# ---------------------------------------------------------------------------
log "starting fixture network+postgres (project=${PROJECT})"
docker network inspect "$FIXNET" >/dev/null 2>&1 || docker network create "$FIXNET" >/dev/null
docker volume create "$VOLUME" >/dev/null

docker run -d --name "$PG_CONTAINER" --network "$FIXNET" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=drillpg -e POSTGRES_DB=postgres \
    postgres:16 >/dev/null

for _ in $(seq 1 60); do
    docker exec "$PG_CONTAINER" pg_isready -U odoo >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$PG_CONTAINER" pg_isready -U odoo >/dev/null 2>&1 || die "fixture postgres never became ready"

log "installing sale_management into ${DB_NAME} (fixture, no mandatory bundle)"
build_odoo_env_args "$DB_NAME" true sale_management
docker run --rm --name "${PROJECT}-odoo-init" --network "$FIXNET" \
    -v "${VOLUME}:/var/lib/odoo" "${ODOO_ENV_ARGS[@]}" \
    "$ODOO_IMAGE" init

log "bootstrapping admin password via odoo shell"
build_odoo_env_args "$DB_NAME" false base
docker run --rm -i --name "${PROJECT}-odoo-shell" --network "$FIXNET" \
    -v "${VOLUME}:/var/lib/odoo" "${ODOO_ENV_ARGS[@]}" \
    -e FIXTURE_ADMIN_PASS="$FIXTURE_ADMIN_PASS" \
    "$ODOO_IMAGE" shell <<'PYEOF'
import os
pw = os.environ["FIXTURE_ADMIN_PASS"]
u = env["res.users"].search([("login", "=", "admin")], limit=1)
if not u:
    raise SystemExit("admin user not found after init")
u.write({"password": pw})
env.cr.commit()
print("FIXTURE_ADMIN_PASSWORD_SET")
PYEOF

log "starting fixture odoo web server"
docker run -d --name "${PROJECT}-odoo" --network "$FIXNET" \
    -v "${VOLUME}:/var/lib/odoo" "${ODOO_ENV_ARGS[@]}" \
    "$ODOO_IMAGE" odoo >/dev/null

for _ in $(seq 1 120); do
    status="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-odoo" 2>/dev/null || echo "")"
    [ "$status" = "healthy" ] && break
    sleep 2
done
[ "$status" = "healthy" ] || die "fixture odoo never became healthy (last status: ${status:-unknown})"

# ---------------------------------------------------------------------------
# 2. XML-RPC: partner + confirmed sale order + 3 attachments
# ---------------------------------------------------------------------------
log "creating fixture data via XML-RPC"
cat >"${WORKDIR}/fixture_client.py" <<'PYEOF'
import base64
import json
import os
import sys
import xmlrpc.client

url = os.environ["ODOO_URL"]
db = os.environ["ODOO_DB"]
login = os.environ.get("ODOO_LOGIN", "admin")
password = os.environ["ODOO_PASSWORD"]
out_path = os.environ["OUT"]

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, login, password, {})
if not uid:
    print("FATAL: xmlrpc authenticate failed", file=sys.stderr)
    sys.exit(1)

models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def call(model, method, *args, **kwargs):
    return models.execute_kw(db, uid, password, model, method, list(args), kwargs)


partner_id = call("res.partner", "create", {"name": "Drill Fixture Partner"})
product_id = call("product.product", "create", {"name": "Drill Fixture Product", "list_price": 100.0})
order_id = call(
    "sale.order",
    "create",
    {
        "partner_id": partner_id,
        "order_line": [(0, 0, {"product_id": product_id, "product_uom_qty": 1, "price_unit": 100.0})],
    },
)
call("sale.order", "action_confirm", [order_id])

attachment_ids = []
for i in range(3):
    content = f"drill-fixture-attachment-{i}".encode()
    attachment_ids.append(
        call(
            "ir.attachment",
            "create",
            {
                "name": f"drill-fixture-{i}.txt",
                "datas": base64.b64encode(content).decode(),
                "res_model": "sale.order",
                "res_id": order_id,
                "type": "binary",
                "mimetype": "text/plain",
            },
        )
    )

rows = call("ir.attachment", "read", attachment_ids, ["store_fname", "checksum"])
order_count = call("sale.order", "search_count", [])

result = {
    "partner_id": partner_id,
    "product_id": product_id,
    "order_id": order_id,
    "expected_orders": order_count,
    "attachments": rows,
}
with open(out_path, "w") as fh:
    json.dump(result, fh, indent=2)
print(json.dumps(result))
PYEOF

docker run --rm --name "${PROJECT}-xmlrpc" --network "$FIXNET" \
    -v "${WORKDIR}:/work" \
    -e ODOO_URL="http://${PROJECT}-odoo:8069" -e ODOO_DB="$DB_NAME" \
    -e ODOO_PASSWORD="$FIXTURE_ADMIN_PASS" -e OUT=/work/fixture_result.json \
    python:3.12-slim python /work/fixture_client.py \
    || die "xmlrpc fixture population failed"

[ -s "${WORKDIR}/fixture_result.json" ] || die "fixture_result.json was not written"
EXPECTED_ORDERS="$(python3 -c "import json;print(json.load(open('${WORKDIR}/fixture_result.json'))['expected_orders'])")"
ATTACHMENT_COUNT="$(python3 -c "import json;print(len(json.load(open('${WORKDIR}/fixture_result.json'))['attachments']))")"
log "fixture data ready: expected_orders=${EXPECTED_ORDERS} attachments=${ATTACHMENT_COUNT}"

# ---------------------------------------------------------------------------
# 3. Optional: drop one attachment's blob before backup (W7 negative test)
# ---------------------------------------------------------------------------
DROPPED_STORE_FNAME=""
DROPPED_ATTACHMENT_ID=""
if [ -n "$DROP_ATTACHMENT_INDEX" ]; then
    read -r DROPPED_STORE_FNAME DROPPED_ATTACHMENT_ID <<<"$(python3 -c "
import json
data = json.load(open('${WORKDIR}/fixture_result.json'))
row = data['attachments'][${DROP_ATTACHMENT_INDEX}]
print(row['store_fname'], row['id'])
")"
    log "dropping attachment id=${DROPPED_ATTACHMENT_ID} store_fname=${DROPPED_STORE_FNAME} from the filestore before backup"
    docker run --rm -v "${VOLUME}:/var/lib/odoo" --entrypoint rm "$ODOO_IMAGE" \
        -f "/var/lib/odoo/filestore/${DB_NAME}/${DROPPED_STORE_FNAME}"
fi

# Stop the web server before dumping/backing up — releases DB connections
# and avoids the filestore changing mid-snapshot.
docker stop "${PROJECT}-odoo" >/dev/null

# ---------------------------------------------------------------------------
# 4. pg_dump -> s3-local
# ---------------------------------------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_LOCAL="${WORKDIR}/dump.sql.gz"
log "pg_dump ${DB_NAME} -> ${DUMP_LOCAL}"
docker exec "$PG_CONTAINER" sh -c "pg_dump -U odoo -d '${DB_NAME}' -Fc | gzip -9" >"$DUMP_LOCAL"
[ -s "$DUMP_LOCAL" ] || die "pg_dump produced an empty file"

DUMP_PATH="${PG_PREFIX}/${STAMP}.sql.gz"
docker run --rm --network "$S3_NETWORK" -v "${WORKDIR}:/work" \
    -e RCLONE_CONFIG_S3LOCAL_TYPE=s3 -e RCLONE_CONFIG_S3LOCAL_PROVIDER=Other \
    -e RCLONE_CONFIG_S3LOCAL_ENV_AUTH=false \
    -e RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID="$S3_ACCESS_KEY" \
    -e RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY="$S3_SECRET_KEY" \
    -e RCLONE_CONFIG_S3LOCAL_ENDPOINT="http://${S3_HOST}:9000" \
    "$S3_LOCAL_IMAGE" copyto /work/dump.sql.gz "s3local:${PG_BUCKET}/${DUMP_PATH}"
DUMP_TIME="$(date -u -d "${STAMP:0:8} ${STAMP:9:2}:${STAMP:11:2}:${STAMP:13:2}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "${STAMP}")"
log "dump uploaded: ${PG_BUCKET}/${DUMP_PATH}"

# ---------------------------------------------------------------------------
# 5. restic init/backup -> s3-local
# ---------------------------------------------------------------------------
RESTIC_REPO="s3:http://${S3_HOST}:9000/${FS_BUCKET}/${FS_PREFIX}"
log "restic init/backup ${RESTIC_REPO}"
docker run --rm --network "$S3_NETWORK" -v "${VOLUME}:/var/lib/odoo:ro" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    restic/restic:0.19.1 -r "$RESTIC_REPO" init >/dev/null 2>&1 || true
docker run --rm --network "$S3_NETWORK" -v "${VOLUME}:/var/lib/odoo:ro" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    restic/restic:0.19.1 -r "$RESTIC_REPO" backup "/var/lib/odoo/filestore/${DB_NAME}"

SNAPSHOT_JSON="$(docker run --rm --network "$S3_NETWORK" \
    -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    restic/restic:0.19.1 -r "$RESTIC_REPO" snapshots --json latest)"
RESTIC_SNAPSHOT="$(echo "$SNAPSHOT_JSON" | python3 -c "import json,sys; rows=json.load(sys.stdin); print(rows[-1]['short_id'])")"
SNAPSHOT_TIME="$(echo "$SNAPSHOT_JSON" | python3 -c "import json,sys; rows=json.load(sys.stdin); print(rows[-1]['time'][:19]+'Z')")"
log "restic snapshot ${RESTIC_SNAPSHOT} at ${SNAPSHOT_TIME}"

# ---------------------------------------------------------------------------
# 6. Metadata for the caller
# ---------------------------------------------------------------------------
{
    echo "FIXTURE_DB=${DB_NAME}"
    echo "DUMP_PATH=${PG_BUCKET}/${DUMP_PATH}"
    echo "DUMP_TIME=${DUMP_TIME}"
    echo "RESTIC_REPO=${RESTIC_REPO}"
    echo "RESTIC_SNAPSHOT=${RESTIC_SNAPSHOT}"
    echo "SNAPSHOT_TIME=${SNAPSHOT_TIME}"
    echo "EXPECTED_ORDERS=${EXPECTED_ORDERS}"
    echo "ATTACHMENT_COUNT=${ATTACHMENT_COUNT}"
    echo "DROPPED_STORE_FNAME=${DROPPED_STORE_FNAME}"
    echo "DROPPED_ATTACHMENT_ID=${DROPPED_ATTACHMENT_ID}"
} >"$META_OUT"

log "metadata written to ${META_OUT}"
log "done"
