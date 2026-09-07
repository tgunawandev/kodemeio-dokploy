#!/usr/bin/env bash
#
# _dispatch.sh — the three dispatch rules shared by every Kodemeio front door.
#
# CANONICAL FILE. Vendored byte-identical into every repo that has a front
# door. Edit kodemeio-skills/templates/frontdoor/_dispatch.sh and run
# `scripts/frontdoor-sync --write`; `--check` fails when a copy has drifted.
#
# The three rules, in order:
#
#   1. <selector...> <group> <verb> ...  -> <cli> -p <resolved profile> ...
#   2. <reserved word> ...               -> bin/<service>/<word>
#   3. anything else                     -> <cli>, verbatim
#
# Rule 3 is the reason the door never becomes the bottleneck people route
# around: a CLI command that shipped this morning is reachable through the door
# this afternoon, with no registration anywhere.
#
# A repo whose selector is [] (react, fastapi, next: the CLI acts on the
# working tree, not on a remote instance) has no rule 1. Guards still apply,
# keyed on the group alone -- see fd_guard_check.

[[ -n "${_FD_DISPATCH:-}" ]] && return 0
_FD_DISPATCH=1

# ── Reserved words ───────────────────────────────────────────────────────────
# Discovered from disk, so adding a tool is dropping in a file. There is no
# table to forget -- which is how odoo-approval.sh came to be missing from the
# old odoo dispatcher, and how odoo's own README still advertises 13 reserved
# words while 15 sit in bin/odoo/.
fd_is_reserved() { [[ -x "$FD_BIN_DIR/$1" && "$1" != _* ]]; }

fd_reserved_list() {
  local -a tools=(); local f
  [[ -d "$FD_BIN_DIR" ]] || return 0
  for f in "$FD_BIN_DIR"/*; do
    [[ -e "$f" ]] || continue
    fd_is_reserved "$(basename "$f")" && tools+=("$(basename "$f")")
  done
  [[ ${#tools[@]} -gt 0 ]] || return 0
  printf '%s\n' "${tools[@]}" | sort
}

# ── Usage ────────────────────────────────────────────────────────────────────
# Every line below is READ OFF THE SAME DISK AND YAML the dispatcher reads, so
# the help text cannot drift from the behaviour. A hand-typed
# "Reserved: release | clone | dev | accurate | health" line once advertised 5
# words while 13 tools existed -- true the day it was written, stale the moment
# a 6th landed.
fd_usage() {
  local cli arity; cli="$(fd_cli)"; arity="$(fd_selector_arity)"
  local sel; sel="$(fd_selector_names | sed 's/^/</;s/$/>/' | tr '\n' ' ')"
  sel="${sel% }"

  printf '%s — this repo'"'"'s front door to %s.\n\n' "$FD_SERVICE.sh" "${cli:-the repo tools}"
  local desc; desc="$(fd_description)"
  [[ -n "$desc" ]] && printf '%s\n\n' "$desc"

  # Built as a two-column table so the descriptions line up whatever the
  # service name and selector are — a printf with hand-counted padding drifted
  # the moment a repo had a longer name than the one it was tuned on.
  local -a rows=()
  (( arity > 0 )) && rows+=("./$FD_SERVICE.sh $sel <group> <verb> [args...]|targeted")
  rows+=("./$FD_SERVICE.sh <reserved> [args...]|repo tool")
  [[ -n "$cli" ]] && rows+=("./$FD_SERVICE.sh <anything else>|passthrough to $cli")
  # ${#r%%|*} is not valid bash — length and pattern removal cannot combine in
  # one expansion, and it fails at RUNTIME with "bad substitution", not at
  # parse time, so it only shows up when usage is actually printed.
  local w=0 r left
  for r in "${rows[@]}"; do left="${r%%|*}"; (( ${#left} > w )) && w=${#left}; done
  printf 'Usage:\n'
  for r in "${rows[@]}"; do printf '  %-*s   %s\n' "$w" "${r%%|*}" "${r#*|}"; done
  printf '\n'

  if (( arity > 0 )); then
    printf 'Targets (%s):\n' "$(fd_selector_names | tr '\n' '/' | sed 's:/$::')"
    local t label profile
    while IFS= read -r t; do
      [[ -n "$t" ]] || continue
      label="$(fd_label_for "$t")"; profile="$(fd_profile_for "$t")"
      printf '  %-28s %s%s\n' "$t" "${label:-—}" "${profile:+  [profile: $profile]}"
    done < <(fd_targets)
    local safe; safe="$(yq -r '.safe_targets // [] | join(", ")' "$FD_YAML")"
    printf '\nGuarded: write verbs need --yes on every target%s.\n' \
      "${safe:+ except $safe}"
    printf '  --yes is THIS door'"'"'s confirmation and is consumed here, not forwarded.\n'
  else
    printf 'Guarded: write verbs listed in %s.yaml need --yes.\n' "$FD_SERVICE"
  fi

  local reserved; reserved="$(fd_reserved_list | tr '\n' ' ')"
  printf '\nReserved: %s  (see bin/%s/)\n' "${reserved:-none}" "$FD_SERVICE"

  printf '\n  --dry-run       print the resolved command instead of running it\n'
  if fd_cli_has_dry_run; then
    printf '  --kctl-dry-run  forward a real --dry-run to %s (it has one of its own)\n' "$cli"
  fi
  printf '  --kctl-yes      forward a real --yes to %s instead of consuming it\n' "${cli:-the CLI}"
}

# ── Global option handling ───────────────────────────────────────────────────
# Recognised so a leading global cannot be mistaken for the command group:
# `./dokploy.sh kodemeio -q deploy apply x.yaml` must still guard "deploy
# apply", not look up guards for the group "-q" and find nothing to guard.
# Reused inside the guard walk so a value-taking global's VALUE cannot be
# mistaken for a dotted-path segment either.
_fd_load_globals() {
  [[ -n "${_FD_GLOBALS_LOADED:-}" ]] && return 0
  _FD_GLOBALS_LOADED=1
  mapfile -t _FD_VALUE_OPTS < <(fd_global_value_opts)
  mapfile -t _FD_BOOL_OPTS  < <(fd_global_bool_opts)
}

_fd_is_value_opt() { local o; for o in ${_FD_VALUE_OPTS[@]+"${_FD_VALUE_OPTS[@]}"}; do [[ "$1" == "$o" ]] && return 0; done; return 1; }
_fd_is_bool_opt()  { local o; for o in ${_FD_BOOL_OPTS[@]+"${_FD_BOOL_OPTS[@]}"};  do [[ "$1" == "$o" ]] && return 0; done; return 1; }

# fd_split_globals <argv...> -- sets FD_GLOBALS=() and FD_REST=().
# Peels recognised globals off the FRONT of argv (value-taking ones take their
# value with them) until the first token that is not one. That token and
# everything after it becomes FD_REST, untouched -- so FD_REST[0] is always the
# real command group, never a flag sitting in argv[0]'s seat.
fd_split_globals() {
  _fd_load_globals
  FD_GLOBALS=(); FD_REST=()
  local -a args=("$@"); local n=${#args[@]} idx=0 a
  while (( idx < n )); do
    a="${args[$idx]}"
    if _fd_is_bool_opt "$a"; then FD_GLOBALS+=("$a"); idx=$(( idx + 1 )); continue; fi
    if _fd_is_value_opt "$a"; then
      if (( idx + 1 >= n )); then c_red "Global option '$a' needs a value."; exit 2; fi
      FD_GLOBALS+=("$a" "${args[$(( idx + 1 ))]}"); idx=$(( idx + 2 )); continue
    fi
    break
  done
  FD_REST=(${args[@]+"${args[@]:$idx}"})
}

# ── Write guard ──────────────────────────────────────────────────────────────
# fd_guard_check <target> <argv...> — exits 2 when a guarded write verb runs on
# a target that is not in safe_targets, without --yes.
#
# It walks argv building a dotted path ("build", then "build.sql", then
# "build.sql.role") and asks the YAML for that path's write verbs at each depth.
# Descending matters: `deploy apply` has "apply" at argv[1], but
# `report build sql apply` has it at argv[3], and a guard that reads only
# argv[1] lets that one straight through.
#
# argv[0] here is always the true group -- callers must pass FD_REST from
# fd_split_globals, never raw argv -- so a leading global can never stand in
# for it.
fd_guard_check() {
  FD_GUARD_KEY=""
  local target="$1"; shift

  # --help never writes, so it is never guarded. Without this the guard denies
  # `<group> <verb> --help` — because the argv walk skips flags and then matches
  # the verb — while fd_guard_deny's own message tells you to run exactly that
  # to check before writing. The door contradicting its own advice teaches people
  # that the guard is noise.
  local _h
  for _h in "$@"; do [[ "$_h" == "--help" || "$_h" == "-h" ]] && return 0; done

  [[ -n "$target" ]] && fd_is_safe_target "$target" && return 0

  local group="$1"; shift
  local has_yes=0 a
  for a in "$@"; do [[ "$a" == "--yes" ]] && has_yes=1; done

  # The group itself may BE the write (`pnpm publish`), in which case there is
  # no verb for the walk below to match on.
  if fd_is_guarded_group "$group"; then
    FD_GUARD_KEY="$group"
    (( has_yes )) || fd_guard_deny "$target" "$group"
    return 0
  fi

  local path="" verbs="" skip_value=0
  for a in "$@"; do
    if (( skip_value )); then skip_value=0; continue; fi
    if [[ "$a" == -* ]]; then
      _fd_is_value_opt "$a" && skip_value=1
      continue
    fi
    # The first iteration queries the root path (`_`), covering verbs that sit
    # directly after the group name, e.g. `approvals approve`.
    verbs="$(fd_guard_verbs "$group" "$path")"
    if [[ -n "$verbs" && " $verbs " == *" $a "* ]]; then
      # Record the exact leaf that matched, so the --yes decision below is
      # made at the same precision the guard was: `users.deactivate` owns a
      # --yes, `users.tokens.create` does not, and they share a group.
      FD_GUARD_KEY="${group}.${path:+$path.}$a"
      (( has_yes )) || fd_guard_deny "$target" "$group ${path:+$path }$a"
      return 0
    fi
    path="${path:+$path.}$a"
  done
  return 0
}

fd_guard_deny() {  # <target> <label>
  local where="${1:+ on $1}"
  c_red "$2 writes${where}. Re-run with --yes."
  c_dim "--yes is this door's confirmation. It is consumed here and never"
  c_dim "reaches $(fd_cli), so it is safe on a verb that has no --yes of its own."
  c_dim "Check first — these never write:"
  c_dim "  ./$FD_SERVICE.sh ${1:+$1 }<group> --help"
  c_dim "  ./$FD_SERVICE.sh ${1:+$1 }<group> <verb> --dry-run"
  exit 2
}

# ── Main ─────────────────────────────────────────────────────────────────────
fd_main() {
  local cli arity; cli="$(fd_cli)"; arity="$(fd_selector_arity)"

  if [[ $# -eq 0 ]]; then fd_usage; exit 0; fi

  if [[ "$1" == "help" && -n "${2:-}" ]]; then
    if fd_is_reserved "$2"; then exec "$FD_BIN_DIR/$2" --help; fi
    shift; need "$cli"; exec "$cli" "$@" --help
  fi
  if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    fd_usage
    if [[ -n "$cli" ]] && command -v "$cli" >/dev/null 2>&1; then echo; exec "$cli" --help; fi
    exit 0
  fi

  # ── Rule 1: <selector...> <group> ... ──────────────────────────────────────
  local key="" i ok=1
  if (( arity > 0 && $# >= arity )); then
    for (( i = 1; i <= arity; i++ )); do key="${key:+$key/}${!i}"; done
    fd_is_target "$key" || ok=0
  else
    ok=0
  fi

  if (( ok )); then
    local target="$key" profile
    shift "$arity"
    profile="$(fd_profile_for "$target")"

    # yes_is_theirs: --kctl-yes was given, so the --yes in argv belongs to the
    # CLI and the door must not eat it. Symmetric with --kctl-dry-run.
    local dry=0 yes_is_theirs=0; local -a pass=()
    # --kctl-dry-run survives the split as a literal --dry-run, so a CLI verb
    # with its OWN (network-touching) preview is still reachable through this
    # door without changing what a bare --dry-run means here.
    for a in "$@"; do
      if [[ "$a" == "--dry-run" ]]; then dry=1
      elif [[ "$a" == "--kctl-dry-run" ]]; then pass+=("--dry-run")
      elif [[ "$a" == "--kctl-yes" ]]; then pass+=("--yes"); yes_is_theirs=1
      else pass+=("$a"); fi
    done
    # Checked AFTER the split: `./x.sh prod --dry-run` has $# >= 1 but leaves
    # pass empty, and ${pass[0]} under `set -u` would abort with an
    # unbound-variable error instead of this message.
    (( ${#pass[@]} >= 1 )) || { c_red "Need a command group after '${target//\// }'"; fd_usage; exit 1; }

    fd_split_globals ${pass[@]+"${pass[@]}"}
    (( ${#FD_REST[@]} >= 1 )) || { c_red "Need a command group after '${target//\// }'"; fd_usage; exit 1; }
    if [[ "${FD_REST[0]}" == -* ]]; then
      c_red "Unrecognised global option '${FD_REST[0]}'. Refusing to guess at the command group — check $cli --help."
      exit 2
    fi
    local g
    for g in ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"}; do
      if [[ "$g" == "-p" || "$g" == "--profile" ]]; then
        c_red "-p/--profile conflicts with the profile '${target//\// }' resolves (${profile:-none}). Drop it."
        exit 1
      fi
    done

    local group="${FD_REST[0]}"

    # A reserved word is ambiguous in the GROUP position only when the tool it
    # points at ALSO parses the selector as its own leading arguments AND the
    # CLI owns a group by the same name -- that is what creates two live,
    # differently-behaving paths for identical typed words. Listed per repo in
    # <service>.yaml `ambiguous:`, because it cannot be derived without
    # invoking each tool.
    local amb
    while IFS= read -r amb; do
      [[ -n "$amb" && "$group" == "$amb" ]] || continue
      c_red "\`$group\` is a repo tool here, not a $cli group."
      c_dim "Use:  ./$FD_SERVICE.sh $group ${target//\// } ${FD_REST[*]:1}"
      exit 2
    done < <(fd_ambiguous_groups)

    fd_guard_check "$target" ${FD_REST[@]+"${FD_REST[@]}"}

    local -a args=(${FD_REST[@]+"${FD_REST[@]}"})

    # Consume --yes unless this group's CLI command owns one. fd_guard_check
    # above has already READ it, so the gate is satisfied; forwarding it to a
    # verb with no such option makes the CLI exit on "No such option: --yes"
    # and the verb unreachable. Stripping happens AFTER the guard and AFTER
    # --dry-run's printout is built, so a dry run prints what would run.
    if (( ! yes_is_theirs )) && ! fd_forwards_yes "${FD_GUARD_KEY:-}"; then
      local -a kept=(); local a2
      for a2 in ${args[@]+"${args[@]}"}; do [[ "$a2" == "--yes" ]] || kept+=("$a2"); done
      args=(${kept[@]+"${kept[@]}"})
    fi

    if (( dry )); then
      if [[ -n "$profile" ]]; then printf '%s -p %s' "$cli" "$profile"; else printf '%s' "$cli"; fi
      printf ' %q' ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"} ${args[@]+"${args[@]}"}
      printf '\n'
      exit 0
    fi

    need "$cli" "Install it from kodemeio-skills/packages/$cli"
    c_dim "▶ ${target}  ${profile:+profile=$profile  }${args[0]}"
    if [[ -n "$profile" ]]; then
      exec "$cli" -p "$profile" ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"} ${args[@]+"${args[@]}"}
    fi
    exec "$cli" ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"} ${args[@]+"${args[@]}"}
  fi

  # ── Rule 2: reserved word -> bin/<service>/<name> ─────────────────────────
  if fd_is_reserved "$1"; then
    local tool="$1"; shift
    exec "$FD_BIN_DIR/$tool" "$@"
  fi

  # ── Rule 3: everything else -> the CLI, verbatim ──────────────────────────
  # A selector-less repo still guards, keyed on the group alone: the CLI acts on
  # the working tree and a `deploy`/`docker` write is as real there as on a
  # remote instance.
  if (( arity == 0 )); then
    local dry0=0 yes0_is_theirs=0; local -a pass0=()
    for a in "$@"; do
      if [[ "$a" == "--dry-run" ]]; then dry0=1
      elif [[ "$a" == "--kctl-dry-run" ]]; then pass0+=("--dry-run")
      elif [[ "$a" == "--kctl-yes" ]]; then pass0+=("--yes"); yes0_is_theirs=1
      else pass0+=("$a"); fi
    done
    (( ${#pass0[@]} >= 1 )) || { fd_usage; exit 1; }
    fd_split_globals ${pass0[@]+"${pass0[@]}"}
    if (( ${#FD_REST[@]} >= 1 )) && [[ "${FD_REST[0]}" != -* ]]; then
      fd_guard_check "" ${FD_REST[@]+"${FD_REST[@]}"}
      local -a a0=(${FD_REST[@]+"${FD_REST[@]}"})
      if (( ! yes0_is_theirs )) && ! fd_forwards_yes "${FD_GUARD_KEY:-}"; then
        local -a k0=(); local a3
        for a3 in ${a0[@]+"${a0[@]}"}; do [[ "$a3" == "--yes" ]] || k0+=("$a3"); done
        a0=(${k0[@]+"${k0[@]}"})
      fi
      if (( dry0 )); then
        printf '%s' "$cli"; printf ' %q' ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"} ${a0[@]+"${a0[@]}"}; printf '\n'
        exit 0
      fi
      need "$cli" "Install it from kodemeio-skills/packages/$cli"
      exec "$cli" ${FD_GLOBALS[@]+"${FD_GLOBALS[@]}"} ${a0[@]+"${a0[@]}"}
    fi
  fi

  [[ -n "$cli" ]] || { c_red "No CLI configured in $FD_SERVICE.yaml and '$1' is not a reserved word."; fd_usage; exit 1; }
  need "$cli" "Install it from kodemeio-skills/packages/$cli"
  exec "$cli" "$@"
}
