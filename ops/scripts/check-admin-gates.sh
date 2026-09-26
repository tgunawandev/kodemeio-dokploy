#!/usr/bin/env bash
# check-admin-gates.sh — prove every kod admin UI refuses an unauthenticated
# request (Wave 0 spec D10). Run after every deploy that touches Traefik,
# Authentik, Dokploy, Hatchet, LiteLLM or dsh; Gatus checks the same gates
# continuously but cannot see a redirect's target host — this script can.
#
# Usage:
#   check-admin-gates.sh [--targets FILE] [--resolve-to HOST:PORT]
#
# Targets: one "<url> <expectation>" per line (# comments, blank lines ok).
# Expectations:
#   expect-gated        302/307 whose Location host is $GATE_AUTH_HOST
#                       (default auth.kodeme.io), or a same-host redirect to
#                       /outpost.goauthentik.io/start; or 401/403
#   expect-404          404 (route must not exist on this host)
#   expect-401-or-403   401 or 403 (bearer-only API, never 200 without a token)
# Any 200 is a FAIL whatever the expectation.
#
# --resolve-to sends every request to HOST:PORT while keeping the URL's
# Host/SNI (curl --connect-to) — used by the local test.
#
# Output: "GATE <url> <status> <location-host> OK|FAIL" per target.
# Exit: 0 all OK, 1 any FAIL, 2 usage error. Needs curl. Sends no cookies or
# credentials, follows no redirects.
set -uo pipefail

HATCHET_HOST="${HATCHET_HOST:-hatchet.kodeme.io}"
GATE_AUTH_HOST="${GATE_AUTH_HOST:-auth.kodeme.io}"
TARGETS_FILE=""
RESOLVE_TO=""

usage() { sed -n '2,25p' "$0" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --targets) [ $# -ge 2 ] || usage; TARGETS_FILE="$2"; shift 2 ;;
        --resolve-to) [ $# -ge 2 ] || usage; RESOLVE_TO="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

default_targets() {
    cat <<EOF
https://dokploy.kodeme.io/            expect-gated
https://dokploy.kodeme.io/api/trpc/   expect-gated
https://dokploy-api.kodeme.io/api/auth/get-session   expect-404
https://dokploy-api.kodeme.io/        expect-404
https://${HATCHET_HOST}/             expect-gated
https://${HATCHET_HOST}/api/v1/meta  expect-401-or-403
https://llm.kodeme.io/ui              expect-gated
https://llm.kodeme.io/key/list        expect-gated
https://dsh.kodeme.io/                expect-gated
EOF
}

if [ -n "$TARGETS_FILE" ]; then
    [ -r "$TARGETS_FILE" ] || { echo "cannot read targets file: $TARGETS_FILE" >&2; exit 2; }
    targets="$(cat "$TARGETS_FILE")"
else
    targets="$(default_targets)"
fi

connect_args=()
if [ -n "$RESOLVE_TO" ]; then
    connect_args=(--connect-to "::${RESOLVE_TO}")
fi

# host part of a Location value ("" for a relative one)
loc_host() {
    local loc="$1"
    case "$loc" in
        http://*|https://*) loc="${loc#*://}"; loc="${loc%%/*}"; loc="${loc%%:*}"; printf '%s' "$loc" ;;
        *) printf '' ;;
    esac
}

loc_path() {
    local loc="$1"
    case "$loc" in
        http://*|https://*) loc="${loc#*://}"; case "$loc" in */*) printf '/%s' "${loc#*/}" ;; *) printf '/' ;; esac ;;
        *) printf '%s' "$loc" ;;
    esac
}

fails=0
checked=0
while read -r url expect _rest; do
    case "$url" in ''|'#'*) continue ;; esac
    checked=$((checked + 1))
    out="$(curl -sS -o /dev/null --max-time 15 "${connect_args[@]}" \
        -w '%{http_code} %{redirect_url}' "$url" 2>/dev/null)" || true
    status="${out%% *}"
    location="${out#* }"
    [ "$location" = "$out" ] && location=""
    # curl resolves a relative Location against the request URL, so a
    # same-host redirect shows up with the request's own host.
    lhost="$(loc_host "$location")"
    rhost="$(loc_host "$url")"
    lpath="$(loc_path "$location")"
    verdict=FAIL
    case "$status" in
        200|000|"") verdict=FAIL ;;
        *)
            case "$expect" in
                expect-gated)
                    case "$status" in
                        401|403) verdict=OK ;;
                        302|307)
                            if [ "$lhost" = "$GATE_AUTH_HOST" ]; then verdict=OK
                            else
                                # the outpost start path counts only on the SAME host
                                # (a relative redirect), never on a third-party host
                                if [ "$lhost" = "$rhost" ]; then
                                    case "$lpath" in /outpost.goauthentik.io/start*) verdict=OK ;; esac
                                fi
                            fi ;;
                    esac ;;
                expect-404) [ "$status" = 404 ] && verdict=OK ;;
                expect-401-or-403) case "$status" in 401|403) verdict=OK ;; esac ;;
                *) echo "unknown expectation '$expect' for $url" >&2 ;;
            esac ;;
    esac
    printf 'GATE %s %s %s %s\n' "$url" "${status:-000}" "${lhost:--}" "$verdict"
    [ "$verdict" = OK ] || fails=$((fails + 1))
done <<< "$targets"

if [ "$checked" -eq 0 ]; then
    echo "no targets" >&2
    exit 2
fi
echo "SUMMARY checked=$checked failed=$fails"
[ "$fails" -eq 0 ]
