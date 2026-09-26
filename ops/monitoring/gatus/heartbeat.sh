#!/bin/sh
# Gatus dead-man heartbeat (spec D5/D6).
#
# Every HEARTBEAT_INTERVAL seconds (default 60): if Gatus answers its own
# /health, ping the Healthchecks.io check; otherwise ping <check>/fail. When
# this sidecar, Gatus, the monitor host or Hetzner itself is gone, the pings
# stop and Healthchecks.io alerts (Telegram + email) from outside Hetzner.
#
# The ping URL is a credential (anyone holding it can silence the alarm), so
# it is never echoed: log lines say "ping ok" / "ping fail", not the URL.
set -u

: "${HC_GATUS_URL:?HC_GATUS_URL is required -- without it nobody hears that Gatus died}"
GATUS_HEALTH_URL="${GATUS_HEALTH_URL:-http://gatus:8080/health}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-60}"

ping_hc() {
    # $1 = "" (success) or "/fail"
    if wget -q -T 10 -O /dev/null "${HC_GATUS_URL%/}$1"; then
        return 0
    fi
    echo "$(date -u +%FT%TZ) HC-PING-FAILED${1:+ (fail)}"
    return 1
}

while :; do
    if wget -q -T 10 -O /dev/null "$GATUS_HEALTH_URL"; then
        ping_hc "" && echo "$(date -u +%FT%TZ) gatus up, ping ok"
        touch /tmp/heartbeat.ok
    else
        ping_hc "/fail" && echo "$(date -u +%FT%TZ) gatus DOWN, ping fail sent"
        rm -f /tmp/heartbeat.ok
    fi
    sleep "$HEARTBEAT_INTERVAL"
done
