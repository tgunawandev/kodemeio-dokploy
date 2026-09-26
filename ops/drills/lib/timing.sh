#!/usr/bin/env bash
# timing.sh — step timer for restore drills.
#
# Sourced (not executed) by drill-odoo.sh. Each step is bracketed by
# `t_start <name>` / `t_end <name> <status>`; `t_end` appends one JSON line
# to $TIMING_FILE:
#   {"name":"fetch_dump","seconds":12.345,"status":"ok","ended_at":"2026-09-26T10:00:00Z"}
#
# `t_steps_json` turns the accumulated JSON-lines file into a JSON array for
# embedding in the drill's results.json under `steps`.
#
# Requires bash (associative arrays) and python3 (JSON assembly only — never
# for timing arithmetic, which is plain `date`/awk so a missing python3
# cannot silently corrupt a duration).

set -u

declare -gA _TIMING_STARTS

# t_start <name> — record the start time for a named step.
t_start() {
    local name="${1:?t_start requires a step name}"
    _TIMING_STARTS["$name"]="$(date +%s.%N)"
    echo "[timing] start ${name}" >&2
}

# t_end <name> <status> — compute elapsed seconds since the matching
# t_start and append a JSON line to $TIMING_FILE. status is a free-form
# word (ok|failed|skipped|...); the caller decides what it means.
t_end() {
    local name="${1:?t_end requires a step name}"
    local status="${2:-ok}"
    local start end seconds file

    start="${_TIMING_STARTS[$name]:-}"
    if [ -z "$start" ]; then
        echo "[timing] WARNING: t_end ${name} called without a matching t_start" >&2
        start="$(date +%s.%N)"
    fi
    end="$(date +%s.%N)"
    seconds="$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.3f", (e - s) }')"

    file="${TIMING_FILE:?TIMING_FILE must be set before sourcing timing.sh}"
    printf '{"name":"%s","seconds":%s,"status":"%s","ended_at":"%s"}\n' \
        "$name" "$seconds" "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$file"

    echo "[timing] end ${name} status=${status} seconds=${seconds}" >&2
}

# t_steps_json [file] — render the JSON-lines timing file as a JSON array.
# Defaults to $TIMING_FILE. Prints "[]" if the file is empty or absent.
t_steps_json() {
    local file="${1:-${TIMING_FILE:-}}"
    if [ -z "$file" ] || [ ! -s "$file" ]; then
        echo "[]"
        return 0
    fi
    python3 - "$file" <<'PY'
import json
import sys

path = sys.argv[1]
steps = []
with open(path) as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        steps.append(json.loads(line))
print(json.dumps(steps))
PY
}

# t_step_seconds <name> [file] — the recorded seconds for one step, or empty
# if it never ran. Used for the RTO total and the results table.
t_step_seconds() {
    local name="${1:?t_step_seconds requires a step name}"
    local file="${2:-${TIMING_FILE:-}}"
    [ -n "$file" ] && [ -s "$file" ] || return 0
    python3 - "$file" "$name" <<'PY'
import json
import sys

path, name = sys.argv[1], sys.argv[2]
with open(path) as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("name") == name:
            print(row.get("seconds", ""))
PY
}
