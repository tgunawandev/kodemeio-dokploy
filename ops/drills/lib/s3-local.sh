#!/usr/bin/env bash
# s3-local.sh — local S3 stand-in for restore-drill tests, via `rclone serve
# s3` (never used for anything but local fixtures/tests — real drills read
# from B2).
#
# Flags verified 2026-09-26 against the pinned image, since no task-6-report
# existed yet to read them from:
#   docker run --rm rclone/rclone:1.75.1 serve s3 --help
# relevant output:
#   --addr stringArray       IPaddress:Port or :Port to bind to (default 127.0.0.1:8080)
#   --auth-key stringArray   Set key pair for v4 authorization: access_key_id,secret_access_key
#   --force-path-style       If true use path style access (default true)
#
# The stand-in never publishes a host port. Every caller (make-fixture.sh,
# drill-odoo.sh) is a one-shot container joined to the same Docker network,
# addressing it by container name — the same no-ports pattern the Task 6
# Authentik backup test override uses for its own `s3-local` service.

S3_LOCAL_IMAGE="${S3_LOCAL_IMAGE:-rclone/rclone:1.75.1}"

# s3_local_start <container> <network> <data_dir> <access_key> <secret_key>
#
# Starts the stand-in server. `data_dir`'s immediate subdirectories become S3
# buckets (rclone's local-backend-as-S3 path-style layout) — create them with
# plain `mkdir -p` before uploading, no S3 "create bucket" call needed.
s3_local_start() {
    local name="$1" network="$2" data_dir="$3" access_key="$4" secret_key="$5"

    mkdir -p "$data_dir"
    docker network inspect "$network" >/dev/null 2>&1 || docker network create "$network" >/dev/null
    docker rm -f "$name" >/dev/null 2>&1 || true

    docker run -d --name "$name" --network "$network" \
        -v "${data_dir}:/data" \
        "$S3_LOCAL_IMAGE" serve s3 /data \
        --addr :9000 \
        --auth-key "${access_key},${secret_key}" \
        >/dev/null
}

# s3_local_rclone <network> <access_key> <secret_key> <endpoint_host> -- <rclone args...>
#
# Runs a one-shot rclone client container against the stand-in without ever
# leaving the given Docker network (no host port, no host rclone install).
s3_local_rclone() {
    local network="$1" access_key="$2" secret_key="$3" endpoint_host="$4"
    shift 4
    docker run --rm --network "$network" \
        -e RCLONE_CONFIG_S3LOCAL_TYPE=s3 \
        -e RCLONE_CONFIG_S3LOCAL_PROVIDER=Other \
        -e RCLONE_CONFIG_S3LOCAL_ENV_AUTH=false \
        -e RCLONE_CONFIG_S3LOCAL_ACCESS_KEY_ID="${access_key}" \
        -e RCLONE_CONFIG_S3LOCAL_SECRET_ACCESS_KEY="${secret_key}" \
        -e RCLONE_CONFIG_S3LOCAL_ENDPOINT="http://${endpoint_host}:9000" \
        "$S3_LOCAL_IMAGE" "$@"
}

# s3_local_wait_ready <network> <access_key> <secret_key> <endpoint_host> [tries]
s3_local_wait_ready() {
    local network="$1" access_key="$2" secret_key="$3" endpoint_host="$4" tries="${5:-30}" i=0
    while [ "$i" -lt "$tries" ]; do
        if s3_local_rclone "$network" "$access_key" "$secret_key" "$endpoint_host" \
            lsd s3local: >/dev/null 2>&1; then
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    return 1
}

# s3_local_stop <container> [network]
s3_local_stop() {
    local name="$1" network="${2:-}"
    docker rm -f "$name" >/dev/null 2>&1 || true
    if [ -n "$network" ]; then
        docker network rm "$network" >/dev/null 2>&1 || true
    fi
}
