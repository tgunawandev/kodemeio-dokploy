#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034  # check() evals single-quoted assertions on purpose
# Tests for ops/scripts/supabase-export.sh with fake `supabase` and `rclone`.
#   bash ops/scripts/tests/test_supabase_export.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../supabase-export.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
ok()   { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else fail "$1"; fi; }

PW='ZZSECRET-MARKER-pw%40x'
URL="postgresql://postgres.abc:${PW}@db.example.invalid:5432/postgres"

mkdir -p "$TMP/bin"
# fake supabase: records argv (as a call log), writes the -f file; FAIL_DATA=1
# makes the data dump fail AND echo its full argv (URL included) to stderr, the
# way a careless CLI error message would.
cat > "$TMP/bin/supabase" <<'FAKE'
#!/usr/bin/env bash
echo "supabase $*" >> "$CALLS"
out=""; kind=schema
while [ $# -gt 0 ]; do
  case "$1" in -f) out="$2"; shift ;; --role-only) kind=roles ;; --data-only) kind=data ;; esac
  shift
done
if [ "$kind" = data ] && [ "${FAIL_DATA:-0}" = 1 ]; then
  echo "error: could not connect using $(tail -1 "$CALLS")" >&2
  exit 3
fi
echo "-- $kind dump" > "$out"
echo "Dumped $kind to $out"
FAKE
cat > "$TMP/bin/rclone" <<'FAKE'
#!/usr/bin/env bash
echo "rclone $*" >> "$CALLS"
if [ "$1" = copy ] && [ "$2" = "sb:" ]; then
  mkdir -p "$3/avatars/u1" "$3/docs"
  printf 'png-bytes' > "$3/avatars/u1/a.png"
  printf 'pdf-bytes' > "$3/docs/b.pdf"
fi
exit 0
FAKE
chmod +x "$TMP/bin/"*

runit() { # outfile, args... (env set by caller)
    local out="$1"; shift
    PATH="$TMP/bin:$PATH" CALLS="$TMP/calls" bash "$SCRIPT" "$@" > "$out" 2>&1
}

echo "--- usage ---"
: > "$TMP/calls"
runit "$TMP/o0"; rc=$?
check "no --project-ref -> exit 2" '[ "$rc" = 2 ]'
runit "$TMP/o0b" --project-ref 'bad;ref'; rc=$?
check "unsafe --project-ref rejected" '[ "$rc" = 2 ]'

echo "--- dry-run is the default ---"
: > "$TMP/calls"
SUPABASE_DB_URL="$URL" runit "$TMP/o1" --project-ref etxref --name terakidz --work-dir "$TMP/w1"; rc=$?
check "dry-run exit 0" '[ "$rc" = 0 ]'
check "dry-run calls nothing" '[ ! -s "$TMP/calls" ]'
check "dry-run prints the plan" 'grep -q "DRY-RUN: nothing executed" "$TMP/o1"'
check "default out-remote uses --name" 'grep -q "b2:kod-prod-backup/supabase/terakidz/[0-9T]*Z/" "$TMP/o1"'
check "default storage endpoint from ref" 'grep -q "https://etxref.supabase.co/storage/v1/s3" "$TMP/o1"'
check "dry-run shows masked URL" 'grep -q "postgresql://postgres.abc:\*\*\*\*@db.example.invalid:5432/postgres" "$TMP/o1"'
check "dry-run never shows the password" '! grep -q "ZZSECRET-MARKER" "$TMP/o1"'

echo "--- execute: missing env refuses before any call ---"
: > "$TMP/calls"
runit "$TMP/o2" --project-ref etxref --execute --work-dir "$TMP/w2"; rc=$?
check "no SUPABASE_DB_URL -> exit 2" '[ "$rc" = 2 ]'
check "...and nothing called" '[ ! -s "$TMP/calls" ]'
SUPABASE_DB_URL="$URL" runit "$TMP/o2b" --project-ref etxref --execute --work-dir "$TMP/w2"; rc=$?
check "no storage keys -> exit 2" '[ "$rc" = 2 ] && [ ! -s "$TMP/calls" ]'

export RCLONE_CONFIG_SB_ACCESS_KEY_ID=ak RCLONE_CONFIG_SB_SECRET_ACCESS_KEY=sk

echo "--- execute: happy path ---"
: > "$TMP/calls"
SUPABASE_DB_URL="$URL" runit "$TMP/o3" --project-ref etxref --name terakidz --execute \
    --work-dir "$TMP/w3" --out-remote "b2:kod-prod-backup/supabase/terakidz/T/"; rc=$?
check "execute exit 0" '[ "$rc" = 0 ]'
order="$(sed -E 's/--db-url [^ ]+ //; s#'"$TMP"'#TMP#g' "$TMP/calls")"
expected="supabase db dump --role-only -f TMP/w3/roles.sql
supabase db dump -f TMP/w3/schema.sql
supabase db dump --data-only --use-copy -f TMP/w3/data.sql
rclone copy sb: TMP/w3/storage
rclone copy TMP/w3 b2:kod-prod-backup/supabase/terakidz/T/
rclone check --one-way TMP/w3 b2:kod-prod-backup/supabase/terakidz/T/"
check "call order roles, schema, data, storage, upload, check" '[ "$order" = "$expected" ]'
[ "$order" = "$expected" ] || diff <(echo "$expected") <(echo "$order")
check "every dump got --db-url" '[ "$(grep -c -- "--db-url" "$TMP/calls")" = 3 ]'
check "output has RESULT=ok" 'grep -q "RESULT=ok" "$TMP/o3"'
check "output never shows the password or URL" '! grep -q "ZZSECRET-MARKER" "$TMP/o3" && ! grep -qF "$URL" "$TMP/o3"'
check "manifest lists 5 files with correct sha256" 'python3 - "$TMP/w3" <<PY
import hashlib, json, os, sys
root = sys.argv[1]
m = json.load(open(os.path.join(root, "manifest.json")))
paths = [f["path"] for f in m["files"]]
assert paths == ["data.sql", "roles.sql", "schema.sql", "storage/avatars/u1/a.png", "storage/docs/b.pdf"], paths
for f in m["files"]:
    b = open(os.path.join(root, f["path"]), "rb").read()
    assert f["sha256"] == hashlib.sha256(b).hexdigest() and f["bytes"] == len(b), f
assert m["project_ref"] == "etxref" and m["name"] == "terakidz"
PY'
check "manifest holds no secret" '! grep -q "ZZSECRET-MARKER" "$TMP/w3/manifest.json"'

echo "--- execute: a failing dump uploads nothing and leaks nothing ---"
: > "$TMP/calls"
FAIL_DATA=1 SUPABASE_DB_URL="$URL" runit "$TMP/o4" --project-ref etxref --execute \
    --work-dir "$TMP/w4" --out-remote "b2:x/y/"; rc=$?
check "failing data dump -> exit 1" '[ "$rc" = 1 ]'
check "no rclone call after the failure" '! grep -q "^rclone" "$TMP/calls"'
check "CLI error line was shown (masked)" 'grep -q "error: could not connect" "$TMP/o4"'
check "the echoed URL/password was masked" '! grep -q "ZZSECRET-MARKER" "$TMP/o4" && grep -q "\*\*\*\*" "$TMP/o4"'

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
