#!/usr/bin/env bash
# Local test for the Dokploy admin gate (W11) and check-admin-gates.sh (W12).
#   bash ops/traefik/tests/test_gates.sh
# Needs docker (compose v2), curl, python3. Creates one compose project and a
# temp dir; both are removed on exit.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
GATE_FILE="$REPO/ops/traefik/dokploy-admin-gate.yml"
CHECK="$REPO/ops/scripts/check-admin-gates.sh"
PROJECT="gatestest$$"
TMP="$(mktemp -d)"
STUB_PID=""
fails=0

ok()   { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }
indent() { while IFS= read -r l; do echo "       $l"; done; }
expect_eq() { if [ "$2" = "$3" ]; then ok "$1 ($3)"; else fail "$1: expected $2, got $3"; fi; }

compose() { DYNAMIC_DIR="$TMP/dynamic" docker compose -p "$PROJECT" -f "$HERE/compose.test.yml" "$@"; }

cleanup() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    compose down -v --remove-orphans >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
echo "--- W12a: check-admin-gates.sh verdict logic (host-run stub, no docker) ---"
SPORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
python3 "$HERE/stub_auth.py" "$SPORT" >/dev/null 2>&1 &
STUB_PID=$!
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$SPORT/_status/204" && break; sleep 0.1; done

gate_case() { # name, expected exit, "<url> <expect>"
    printf '%s\n' "$3" > "$TMP/t"
    out="$(bash "$CHECK" --targets "$TMP/t" --resolve-to "127.0.0.1:$SPORT")"
    rc=$?
    expect_eq "$1 exit" "$2" "$rc"
    indent <<< "$out"
}
gate_case "302 -> auth.kodeme.io is gated" 0 \
    "http://ui.test/_status/302?loc=https://auth.kodeme.io/application/o/authorize/ expect-gated"
gate_case "same-host 302 -> /outpost.goauthentik.io/start is gated" 0 \
    "http://ui.test/_status/302?loc=/outpost.goauthentik.io/start?rd=x expect-gated"
gate_case "401 is gated" 0 "http://ui.test/_status/401 expect-gated"
gate_case "307 -> a third-party host is NOT gated" 1 \
    "http://ui.test/_status/307?loc=https://evil.example/outpost.goauthentik.io/start expect-gated"
gate_case "302 -> a random login page is NOT gated" 1 \
    "http://ui.test/_status/302?loc=https://ui.test/login expect-gated"
gate_case "200 is always FAIL (expect-gated)" 1 "http://ui.test/_status/200 expect-gated"
gate_case "200 is always FAIL (expect-404)" 1 "http://ui.test/_status/200 expect-404"
gate_case "404 satisfies expect-404" 0 "http://ui.test/_status/404 expect-404"
gate_case "403 satisfies expect-401-or-403" 0 "http://ui.test/_status/403 expect-401-or-403"
gate_case "302 does NOT satisfy expect-401-or-403" 1 \
    "http://ui.test/_status/302?loc=https://auth.kodeme.io/ expect-401-or-403"
printf 'http://127.0.0.1:1/ expect-gated\n' > "$TMP/t"
out="$(bash "$CHECK" --targets "$TMP/t")"; rc=$?
expect_eq "unreachable is FAIL" 1 "$rc"
case "$out" in *"GATE http://127.0.0.1:1/ 000 - FAIL"*) ok "unreachable prints status 000";;
    *) fail "unreachable line wrong: $out";; esac
kill "$STUB_PID" 2>/dev/null; STUB_PID=""

# ---------------------------------------------------------------------------
echo "--- remote outpost compose renders; token is guarded ---"
OUTPOST="$REPO/ops/authentik/remote-outpost.compose.yml"
if AUTHENTIK_OUTPOST_TOKEN=dummy docker compose -f "$OUTPOST" config --quiet 2>/dev/null; then
    ok "remote-outpost.compose.yml renders with a token"
else
    fail "remote-outpost.compose.yml does not render"
fi
if env -u AUTHENTIK_OUTPOST_TOKEN docker compose -f "$OUTPOST" config --quiet 2>/dev/null; then
    fail "remote outpost renders WITHOUT a token (guard missing)"
else
    ok "missing AUTHENTIK_OUTPOST_TOKEN refuses to render"
fi
rendered="$(AUTHENTIK_OUTPOST_TOKEN=dummy docker compose -f "$OUTPOST" config 2>/dev/null)"
if grep -q 'image: ghcr.io/goauthentik/proxy:2026.2.3' <<< "$rendered" \
    && grep -A3 'test:' <<< "$rendered" | grep -q -- '- /proxy'; then
    ok "pinned proxy image + /proxy healthcheck"
else
    fail "outpost image/healthcheck not as expected"
fi
case "$rendered" in *"published"*) fail "outpost publishes a port";; *) ok "outpost publishes no port";; esac

# ---------------------------------------------------------------------------
echo "--- W11: Traefik with the real gate file (hosts -> *.localtest) ---"
mkdir -p "$TMP/dynamic"
render_gate() { # $1 = extra sed expression (optional)
    sed -e 's/dokploy\.kodeme\.io/dokploy.localtest/g' \
        -e 's/dokploy-api\.kodeme\.io/dokploy-api.localtest/g' \
        -e 's/hatchet\.kodeme\.io/hatchet.localtest/g' \
        -e 's/entryPoints: \[websecure\]/entryPoints: [web]/' \
        -e '/^ *tls: {certResolver: letsencrypt}$/d' \
        ${1:+-e "$1"} "$GATE_FILE" > "$TMP/dynamic/gate.yml.tmp"
    mv "$TMP/dynamic/gate.yml.tmp" "$TMP/dynamic/dokploy-admin-gate.yml"
}
render_gate
if grep -q 'kodeme.io\|websecure\|certResolver' "$TMP/dynamic/dokploy-admin-gate.yml"; then
    fail "rendered gate file still has production hosts/TLS"
fi
# Stand-in for Dokploy's own dynamic file: same router/service names as prod.
cat > "$TMP/dynamic/dokploy.yml" <<'EOF'
http:
  routers:
    dokploy-router-app-secure:
      rule: "Host(`dokploy.localtest`)"
      entryPoints: [web]
      service: dokploy-service-app
  services:
    dokploy-service-app:
      loadBalancer: {servers: [{url: "http://dokploy:3000"}]}
EOF

compose up -d >/dev/null 2>&1 || { fail "compose up"; compose logs; exit 1; }
PORT="$(compose port traefik 80 | sed 's/.*://')"
# ready = Dokploy stand-in AND the stub outpost both answer through Traefik
for _ in $(seq 1 120); do
    c1="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: dokploy-api.localtest' "http://127.0.0.1:$PORT/api/project.all")"
    c2="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: dokploy.localtest' "http://127.0.0.1:$PORT/outpost.goauthentik.io/ping")"
    [ "$c1" = 200 ] && [ "$c2" = 204 ] && break
    sleep 0.5
done

code() { # host path [cookie]
    local args=(-s -o /dev/null -w '%{http_code}' -H "Host: $1")
    [ -n "${3:-}" ] && args+=(-H "Cookie: $3")
    curl "${args[@]}" "http://127.0.0.1:$PORT$2"
}

for p in / /api/trpc/x /api/project.all /settings; do
    expect_eq "UI host $p without session -> 401" 401 "$(code dokploy.localtest "$p")"
    expect_eq "UI host $p with session -> 200 (reaches Dokploy)" 200 "$(code dokploy.localtest "$p" stub=ok)"
done
expect_eq "UI host outpost path is reachable ungated" 204 "$(code dokploy.localtest /outpost.goauthentik.io/ping)"
expect_eq "Hatchet host outpost path reaches the outpost" 204 "$(code hatchet.localtest /outpost.goauthentik.io/ping)"
expect_eq "Hatchet host other paths are NOT routed by this file" 404 "$(code hatchet.localtest /)"
expect_eq "UI host forged cookie still 401" 401 "$(code dokploy.localtest / stub=nope)"
expect_eq "API host /api/project.all -> 200 (upstream)" 200 "$(code dokploy-api.localtest /api/project.all)"
expect_eq "API host /api/deploy/github -> 200 (webhook)" 200 "$(code dokploy-api.localtest /api/deploy/github)"
for p in /api/auth/sign-in /api/auth/get-session /api/trpc/x /api/trpc / /login /api; do
    expect_eq "API host $p -> 404" 404 "$(code dokploy-api.localtest "$p")"
done
body="$(curl -s -H 'Host: dokploy.localtest' -H 'Cookie: stub=ok' "http://127.0.0.1:$PORT/")"
case "$body" in *"Hostname:"*) ok "gated UI request really reached the Dokploy stand-in (whoami body)";;
    *) fail "UI request with session did not reach whoami";; esac

# ---------------------------------------------------------------------------
echo "--- W12b: check-admin-gates.sh against the live local stack ---"
cat > "$TMP/targets" <<'EOF'
# local stand-ins for the production defaults
http://dokploy.localtest/                       expect-gated
http://dokploy.localtest/api/trpc/              expect-gated
http://dokploy-api.localtest/api/auth/get-session   expect-404
http://dokploy-api.localtest/                   expect-404
EOF
out="$(bash "$CHECK" --targets "$TMP/targets" --resolve-to "127.0.0.1:$PORT")"; rc=$?
indent <<< "$out"
expect_eq "check-admin-gates exit with gate in place" 0 "$rc"

# Remove the middleware from the router -> the UI host is open.
render_gate '/^ *middlewares: \[dokploy-authentik\]$/d'
if grep -q 'middlewares: \[dokploy-authentik\]' "$TMP/dynamic/dokploy-admin-gate.yml"; then
    fail "mutation did not remove the middleware"
fi
compose restart traefik >/dev/null 2>&1
PORT="$(compose port traefik 80 | sed 's/.*://')"   # ephemeral port changes on restart
for _ in $(seq 1 60); do
    [ "$(code dokploy.localtest /)" = 200 ] && break
    sleep 0.5
done
out="$(bash "$CHECK" --targets "$TMP/targets" --resolve-to "127.0.0.1:$PORT")"; rc=$?
indent <<< "$out"
expect_eq "check-admin-gates exit with the middleware removed" 1 "$rc"
if echo "$out" | grep -q '^GATE http://dokploy.localtest/ 200 - FAIL$'; then
    ok "failure names the open URL"
else
    fail "failure line for http://dokploy.localtest/ missing"
fi
if echo "$out" | grep -q '^GATE http://dokploy-api.localtest/ 404 - OK$'; then
    ok "unaffected API-host gate still OK"
else
    fail "API-host line changed unexpectedly"
fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
