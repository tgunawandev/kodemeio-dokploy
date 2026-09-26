#!/usr/bin/env bash
# shellcheck disable=SC2015  # ok/fail always return 0, so A && ok || fail is safe here
# Tests for ops/scripts/redaction-grep.sh (W14).
#   bash ops/scripts/tests/test_redaction_grep.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../redaction-grep.sh"
TMP="$(mktemp -d)"
CNAME="redactgrep$$"
cleanup() { docker rm -f "$CNAME" >/dev/null 2>&1; rm -rf "$TMP"; }
trap cleanup EXIT
fails=0
ok()   { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }

printf 'line one\nuser logged in\nall fine\n' > "$TMP/clean.log"
printf 'a\nb\nsecret is ZZSECRET-MARKER-42 oops\nc\nZZPII-MARKER-7 again\n' > "$TMP/dirty.log"

out="$(bash "$SCRIPT" "$TMP/clean.log")"; rc=$?
[ "$rc" = 0 ] && ok "clean file -> exit 0" || fail "clean file -> exit $rc"
out="$(bash "$SCRIPT" "$TMP/clean.log" "$TMP/dirty.log")"; rc=$?
[ "$rc" = 1 ] && ok "planted marker -> exit 1" || fail "planted marker -> exit $rc"
grep -qx "HIT $TMP/dirty.log:3" <<< "$out" && grep -qx "HIT $TMP/dirty.log:5" <<< "$out" \
    && ok "hits name source and line (3, 5)" || fail "hit lines wrong: $out"
grep -q "MARKER\|oops" <<< "$out" && fail "matched line content leaked into output" || ok "matched line never printed"
grep -q "hits=2" <<< "$out" && ok "SUMMARY counts 2 hits" || fail "summary wrong"

bash "$SCRIPT" "$TMP/missing.log" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "unreadable file -> exit 2 (not a silent pass)" || fail "unreadable -> $rc"
bash "$SCRIPT" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "no input -> usage exit 2" || fail "no input -> $rc"

# markers come from the contract, not a hardcoded list
printf "synthetic_markers: ['QQTEST-']\n" > "$TMP/c.yaml"
bash "$SCRIPT" --contract "$TMP/c.yaml" "$TMP/dirty.log" >/dev/null; rc=$?
[ "$rc" = 0 ] && ok "custom contract: default markers not used" || fail "custom contract -> $rc"
printf 'x QQTEST-1\n' > "$TMP/q.log"
bash "$SCRIPT" --contract "$TMP/c.yaml" "$TMP/q.log" >/dev/null; rc=$?
[ "$rc" = 1 ] && ok "custom contract marker found" || fail "custom marker -> $rc"

# without PyYAML (python3 failing) the plain-text fallback must read the same markers
mkdir -p "$TMP/nopy" && printf '#!/bin/sh\nexit 1\n' > "$TMP/nopy/python3" && chmod +x "$TMP/nopy/python3"
out="$(PATH="$TMP/nopy:$PATH" bash "$SCRIPT" "$TMP/dirty.log")"; rc=$?
[ "$rc" = 1 ] && grep -q "hits=2" <<< "$out" && ok "fallback marker parse (no PyYAML) finds both" || fail "fallback: rc=$rc $out"

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    docker run -d --name "$CNAME" alpine:3.20 sh -c 'echo boot; echo "leak ZZPII-MARKER-9" >&2; echo done; sleep 30' >/dev/null
    sleep 2
    out="$(bash "$SCRIPT" --docker "$CNAME")"; rc=$?
    [ "$rc" = 1 ] && grep -q "^HIT docker:$CNAME:[0-9]*$" <<< "$out" && grep -q 'hits=1' <<< "$out" \
        && ok "docker logs (stderr) marker found (line reported; stdout/stderr order is not deterministic)" || fail "docker: rc=$rc out=$out"
    docker rm -f "$CNAME" >/dev/null
    docker run -d --name "$CNAME" alpine:3.20 sh -c 'echo clean; sleep 30' >/dev/null
    sleep 2
    bash "$SCRIPT" --docker "$CNAME" >/dev/null; rc=$?
    [ "$rc" = 0 ] && ok "clean container -> exit 0" || fail "clean container -> $rc"
    bash "$SCRIPT" --docker "no-such-container-$$" >/dev/null 2>&1; rc=$?
    [ "$rc" = 2 ] && ok "missing container -> exit 2" || fail "missing container -> $rc"
else
    echo "skip - docker unavailable"
fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
