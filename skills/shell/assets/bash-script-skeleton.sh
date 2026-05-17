#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Production Bash skeleton. Copy, rename, delete what you do not need.
# Embodies every rule in the shell skill and is ShellCheck clean.
# Verify with: shellcheck -x this-file.sh

set -Eeuo pipefail
IFS=$'\n\t'

# Hard version gate FIRST, in Bash-3-safe syntax only. The logging below
# uses Bash-4 features (local -A, ${v,,}); on macOS's /bin/bash (3.2) that
# would fail with a cryptic error before any later check could explain it.
if (( BASH_VERSINFO[0] < 4 )); then
  echo "this script requires bash >= 4 (have ${BASH_VERSION:-unknown})" >&2
  exit 1
fi

#--- constants ----------------------------------------------------------------
# Pure parameter expansion: no external `basename` (POSIX basename takes
# no options, so `basename --` is non-portable), no subshell, no surprise.
SCRIPT_NAME=${BASH_SOURCE[0]##*/}
readonly SCRIPT_NAME
# Not readonly: --verbose lowers it at parse time. Default from the
# environment, else "info".
LOG_LEVEL="${LOG_LEVEL:-info}"

# Populated in main(); cleaned up by the EXIT trap.
WORKDIR=""

#--- logging (everything non-result goes to stderr) ---------------------------
_log() { # _log LEVEL MESSAGE...
  local level=$1
  shift
  local -A order=([debug]=0 [info]=1 [warn]=2 [error]=3)
  (( order[$level] < order[${LOG_LEVEL,,}] )) && return 0
  printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*" >&2
}
log_debug() { _log debug "$@"; }
log_info() { _log info "$@"; }
log_warn() { _log warn "$@"; }
log_error() { _log error "$@"; }
die() {
  log_error "$@"
  exit 1
}

#--- traps --------------------------------------------------------------------
cleanup() { # idempotent; runs on success, error, and signal
  local rc=$?
  if [[ -n "$WORKDIR" && -d "$WORKDIR" ]]; then
    rm -rf -- "$WORKDIR"
  fi
  return "$rc"
}
trap cleanup EXIT

on_err() { # requires set -E so it fires inside functions/subshells
  local rc=$? line=$1
  log_error "failed (rc=${rc}) at line ${line}: ${BASH_COMMAND}"
  exit "$rc"
}
trap 'on_err "$LINENO"' ERR
trap 'log_warn "interrupted"; exit 130' INT
trap 'log_warn "terminated"; exit 143' TERM

#--- preflight ----------------------------------------------------------------
require() { # require CMD...
  local missing=0 c
  for c in "$@"; do
    if ! command -v "$c" >/dev/null 2>&1; then
      log_error "missing dependency: $c"
      missing=1
    fi
  done
  (( missing )) && die "install the missing dependencies and retry"
  return 0
}

#--- helpers ------------------------------------------------------------------
usage() {
  cat >&2 <<EOF
Usage: ${SCRIPT_NAME} [options] -- ARG...

Options:
  -o, --out PATH   output path (default: stdout)
  -n, --dry-run    log actions without executing them
  -v, --verbose    set log level to debug
  -h, --help       show this help and exit
EOF
}

# run: log a command, then execute it unless DRY_RUN. Takes argv (no eval).
DRY_RUN=0
run() {
  log_info "+ $*"
  (( DRY_RUN )) && return 0
  "$@"
}

# write_atomic DEST < content  (never leaves a half-written file)
write_atomic() {
  local dest=$1 tmp
  tmp=$(mktemp -- "${dest}.XXXXXX")
  if cat >"$tmp"; then
    mv -f -- "$tmp" "$dest"
  else
    rm -f -- "$tmp"
    return 1
  fi
}

#--- main ---------------------------------------------------------------------
main() {
  local out=""
  while (( $# )); do
    case $1 in
      -h | --help) usage; exit 0 ;;
      -n | --dry-run) DRY_RUN=1 ;;
      -v | --verbose) LOG_LEVEL=debug ;;
      -o | --out) out=${2:?--out needs a value}; shift ;;
      --) shift; break ;;
      -*) usage; die "unknown option: $1" ;;
      *) break ;;
    esac
    shift
  done

  require date mktemp

  WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/${SCRIPT_NAME}.XXXXXXXX")
  log_debug "workdir: $WORKDIR dry_run=$DRY_RUN out=${out:-<stdout>}"

  # --- real work goes here. Example: produce a result on STDOUT only. ---
  local result
  result=$(printf 'processed %d argument(s)\n' "$#")

  if [[ -n "$out" ]]; then
    printf '%s' "$result" | write_atomic "$out"
    log_info "wrote $out"
  else
    printf '%s' "$result"
  fi
}

main "$@"
