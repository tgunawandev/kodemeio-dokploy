#!/usr/bin/env bash
# redaction-grep.sh — prove planted synthetic markers never surface in logs or
# files (redaction contract v1, contracts/observability/redaction.v1.yaml; W14).
#
# Usage:
#   redaction-grep.sh [--contract FILE] [--docker CONTAINER]... [FILE|DIR...]
#
# Reads `synthetic_markers` from the contract, greps `docker logs <container>`
# (stdout+stderr) and each FILE. A DIRECTORY is expanded deliberately, one file
# per line, recursively (`find "$dir" -type f`): the runbook's snapshot check
# (ops/runbooks/observability-rollout.md M9) scans the whole kod-metrics volume,
# and `grep -f markers "$dir"` would print "Is a directory" to stderr and still
# report hits=0 — a false clean on the one check that backs the redaction claim.
# A directory the scan cannot read, or one that holds no files, is exit 2, never
# "clean".
#
# On a hit it prints "HIT <source>:<line>" — the source and line number only,
# NEVER the matched line (it may hold the very secret that leaked).
# Exit: 0 clean, 1 any hit, 2 usage / unreadable input / nothing to scan.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRACT="$HERE/../../contracts/observability/redaction.v1.yaml"
containers=()
files=()

# The header above, verbatim: line 2 through the "Exit:" line.
usage() { sed -n '2,/^# Exit:/p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --contract) [ $# -ge 2 ] || usage; CONTRACT="$2"; shift 2 ;;
        --docker) [ $# -ge 2 ] || usage; containers+=("$2"); shift 2 ;;
        -h|--help) usage ;;
        --) shift; files+=("$@"); break ;;
        -*) echo "unknown option: $1" >&2; usage ;;
        *) files+=("$1"); shift ;;
    esac
done
[ ${#containers[@]} -gt 0 ] || [ ${#files[@]} -gt 0 ] || usage
[ -r "$CONTRACT" ] || { echo "cannot read contract: $CONTRACT" >&2; exit 2; }

# markers: the YAML flow list `synthetic_markers: ['A', 'B']` (python/PyYAML
# when available, else a plain parse of that one line)
markers="$(python3 - "$CONTRACT" 2>/dev/null <<'PY'
import sys, yaml
for m in yaml.safe_load(open(sys.argv[1]))["synthetic_markers"]:
    print(m)
PY
)" || markers=""
if [ -z "$markers" ]; then
    markers="$(sed -n "s/^synthetic_markers: *\[\(.*\)\] *$/\1/p" "$CONTRACT" | tr ',' '\n' | sed "s/^ *['\"]//; s/['\"] *$//")"
fi
[ -n "$markers" ] || { echo "no synthetic_markers in $CONTRACT" >&2; exit 2; }
pattern_file="$(mktemp)"
trap 'rm -f "$pattern_file"' EXIT
printf '%s\n' "$markers" > "$pattern_file"

hits=0
sources=0
scan() { # label; reads stdin
    local label="$1" n
    while IFS= read -r n; do
        echo "HIT $label:$n"
        hits=$((hits + 1))
    done < <(grep -n -F -f "$pattern_file" | cut -d: -f1)
}

for c in "${containers[@]}"; do
    if ! docker logs "$c" > /dev/null 2>&1; then
        echo "cannot read docker logs for $c" >&2; exit 2
    fi
    scan "docker:$c" < <(docker logs "$c" 2>&1)
    sources=$((sources + 1))
done
for f in "${files[@]}"; do
    if [ -d "$f" ]; then
        [ -r "$f" ] || { echo "cannot read directory $f" >&2; exit 2; }
        found=0
        while IFS= read -r child; do
            [ -r "$child" ] || { echo "cannot read $child" >&2; exit 2; }
            scan "$child" < "$child"
            found=$((found + 1))
        done < <(find "$f" -type f | LC_ALL=C sort)
        # An empty (or unreadable) tree is not evidence of anything: exit 2
        # rather than report the clean zero the bug above this fix produced.
        [ "$found" -gt 0 ] || { echo "no files under $f" >&2; exit 2; }
        sources=$((sources + found))
    else
        [ -r "$f" ] || { echo "cannot read $f" >&2; exit 2; }
        # shellcheck disable=SC2094  # scan only reads stdin; nothing writes to $f
        scan "$f" < "$f"
        sources=$((sources + 1))
    fi
done

echo "SUMMARY sources=$sources hits=$hits"
[ "$hits" -eq 0 ]
