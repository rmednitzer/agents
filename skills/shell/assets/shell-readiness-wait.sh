#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Defeat the tmux send-keys race: do not guess how long a shell takes to
# finish sourcing rc files (oh-my-zsh, nvm, pyenv, mise, direnv, starship
# can take 1 to 2+ seconds). Instead, prove the shell is ready by sending
# a sentinel and waiting until ITS OUTPUT appears, then it is safe to
# send the real command.
#
# Use this only when you genuinely need an interactive shell in the pane.
# If you know the program up front, run it AS the pane process instead
# (tmux new-session -d 'exec prog') and you do not need this at all.
#
# Usage:  shell-readiness-wait.sh SOCKET PANE_ID [TIMEOUT_SECONDS]
# Then:   tmux -L SOCKET send-keys -t PANE_ID 'real command' Enter
# Exit:   0 ready; 1 real timeout; 2 bad usage; 3 tmux target/socket
#         error (so a stale socket or wrong pane id is NOT misreported
#         as a readiness timeout).
# Verify with: shellcheck -x this-file.sh

set -Eeuo pipefail
IFS=$'\n\t'

usage() { echo "usage: shell-readiness-wait.sh SOCKET PANE_ID [TIMEOUT]" >&2; }

# Explicit validation so bad usage exits 2 (the documented contract), not
# the status 1 a ${x:?} abort would give, which the timeout path also uses.
(( $# >= 2 && $# <= 3 )) || { usage; exit 2; }
sock=$1
pane=$2
timeout_s=${3:-15}
[[ -n "$sock" && -n "$pane" ]] || { usage; exit 2; }
# Validate TIMEOUT before any arithmetic: a non-numeric value would
# otherwise blow up in $(( )) with a raw shell error (status 1).
[[ "$timeout_s" =~ ^[1-9][0-9]*$ ]] || {
  echo "TIMEOUT must be a positive integer (got: $timeout_s)" >&2
  exit 2
}

command -v tmux >/dev/null 2>&1 || { echo "tmux not found" >&2; exit 2; }

# Resolve the target up front. A bad socket or pane id is a configuration
# error (exit 3), categorically different from "the shell was slow to be
# ready" (exit 1). Without this, set -e would abort the first send-keys
# with status 1 and the caller would misread it as a timeout.
tmux -L "$sock" display-message -p -t "$pane" '#{pane_id}' >/dev/null 2>&1 \
  || { echo "tmux target not found: $sock:$pane" >&2; exit 3; }

marker="__SHELL_READY_$$_${RANDOM}__"

# Clear any half-typed line first (C-u), then ask the shell to echo the
# marker. The TYPED line will contain `printf ... __MARKER__`; only the
# EXECUTED output is the marker alone on its own line, so a whole-line
# match (grep -x) cannot be fooled by the command echo.
tmux -L "$sock" send-keys -t "$pane" C-u 2>/dev/null \
  || { echo "tmux send-keys failed: $sock:$pane" >&2; exit 3; }
tmux -L "$sock" send-keys -t "$pane" "printf '%s\\n' '$marker'" Enter 2>/dev/null \
  || { echo "tmux send-keys failed: $sock:$pane" >&2; exit 3; }

deadline=$(( SECONDS + timeout_s ))
until tmux -L "$sock" capture-pane -p -t "$pane" 2>/dev/null \
  | grep -qxF -- "$marker"; do
  if (( SECONDS >= deadline )); then
    echo "shell in $sock:$pane not ready after ${timeout_s}s" >&2
    exit 1
  fi
  sleep 0.1
done

# Optional, cosmetic: scrub the marker lines from the pane. Best-effort:
# readiness is already proven, so a failure here must not change the
# success exit.
tmux -L "$sock" send-keys -t "$pane" C-l 2>/dev/null || true
exit 0
