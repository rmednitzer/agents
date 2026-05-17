#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run a command inside an isolated tmux session and reliably recover its
# full output AND its true exit code, with a bounded wait.
#
# Why this is reliable:
#   * Private socket (-L) and -f /dev/null: never touches the user's
#     server or their ~/.tmux.conf.
#   * The command is the pane's process (passed at creation), so there is
#     NO send-keys race and NO shell rc phase to beat.
#   * The exit code is written to a file and a fixed wait-for channel is
#     signaled; nothing is scraped from a prompt.
#   * wait-for has no native timeout, so it is wrapped in `timeout`.
#   * The private server is torn down by the EXIT trap even on crash.
#
# Usage:   tmux-run-capture.sh [-t SECONDS] -- CMD [ARG...]
# Example: tmux-run-capture.sh -t 120 -- bash -lc 'make test'
# Output:  the captured pane text on stdout; exits with CMD's exit code
#          (124 if it timed out).
#
# On macOS, `timeout` is `gtimeout` (brew install coreutils).
# Verify with: shellcheck -x this-file.sh

set -Eeuo pipefail
IFS=$'\n\t'

TIMEOUT=600
SOCK="cap-$$-${RANDOM}"
SESS="run"
WORKDIR=""

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${*:2}" >&2; }
die() { log error "$*"; exit 1; }

# shellcheck disable=SC2317  # reached only via `trap cleanup EXIT`; not dead code
cleanup() {
  local rc=$?
  tmux -L "$SOCK" kill-server 2>/dev/null || true
  [[ -n "$WORKDIR" && -d "$WORKDIR" ]] && rm -rf -- "$WORKDIR"
  return "$rc"
}
trap cleanup EXIT
trap 'log warn "interrupted"; exit 130' INT
trap 'log warn "terminated"; exit 143' TERM

# --- args ---
while (( $# )); do
  case $1 in
    -t | --timeout) TIMEOUT=${2:?--timeout needs SECONDS}; shift ;;
    --) shift; break ;;
    -h | --help) grep '^#' -- "$0" | cut -c3- >&2; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
  shift
done
(( $# )) || die "no command given (use: ... -- CMD [ARG...])"
command -v tmux >/dev/null 2>&1 || die "tmux not found"
command -v timeout >/dev/null 2>&1 || die "timeout not found"

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/tmux-run.XXXXXXXX")
cmd_file="$WORKDIR/cmd"   # the command, verbatim: no re-quoting needed
rc_file="$WORKDIR/rc"     # the exit code, written by the pane
chan="finished"           # fixed wait-for channel name

# Serialize argv into a script the pane runs by path. Because the pane
# only ever sees the (space-free, mktemp) path, nothing has to be escaped
# back through tmux's shell-word argument.
{
  printf '#!/usr/bin/env bash\nset -o pipefail\n'
  printf '%q ' "$@"
  printf '\n'
} >"$cmd_file"
chmod +x -- "$cmd_file"

# Pane command (run by tmux via `sh -c`). \$? is escaped so it expands in
# the PANE, not here. Embedded paths are safe (mktemp, no spaces/quotes).
pane_cmd="bash '$cmd_file'; echo \$? > '$rc_file'; tmux -L '$SOCK' wait-for -S '$chan'"

# Start the private server, raise history so capture-pane -S - can see
# all output, then create the detached session running pane_cmd.
tmux -L "$SOCK" -f /dev/null \
  set-option -g history-limit 100000 \; \
  new-session -d -s "$SESS" -x 240 -y 60 "$pane_cmd"

pane=$(tmux -L "$SOCK" display-message -p -t "$SESS" '#{pane_id}')
log info "running in $SOCK:$pane (timeout ${TIMEOUT}s)"

# Block until the pane signals completion, but never longer than TIMEOUT.
status=0
if ! timeout "$TIMEOUT" tmux -L "$SOCK" wait-for "$chan"; then
  status=$?
  if (( status == 124 )); then
    log error "timed out after ${TIMEOUT}s"
  else
    log error "wait-for failed (rc=$status)"
  fi
fi

# Full pane text including scrollback (filter our trailing rc/signal noise
# at the call site if needed).
tmux -L "$SOCK" capture-pane -p -S - -t "$pane" || true

# Prefer the recorded exit code over any wait-for error, unless we timed
# out (then keep 124 so callers can tell it was a timeout).
if (( status != 124 )) && [[ -s "$rc_file" ]]; then
  status=$(<"$rc_file")
fi
log info "exit status: $status"
exit "$status"
