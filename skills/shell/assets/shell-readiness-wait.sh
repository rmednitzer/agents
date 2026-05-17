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
# Exit:   0 when ready; 1 on timeout; 2 on bad usage.
# Verify with: shellcheck -x this-file.sh

set -Eeuo pipefail
IFS=$'\n\t'

sock=${1:?usage: shell-readiness-wait.sh SOCKET PANE_ID [TIMEOUT]}
pane=${2:?usage: shell-readiness-wait.sh SOCKET PANE_ID [TIMEOUT]}
timeout_s=${3:-15}

command -v tmux >/dev/null 2>&1 || { echo "tmux not found" >&2; exit 2; }

marker="__SHELL_READY_${$}_${RANDOM}__"

# Clear any half-typed line first (C-u), then ask the shell to echo the
# marker. The TYPED line will contain `printf ... __MARKER__`; only the
# EXECUTED output is the marker alone on its own line, so a whole-line
# match (grep -x) cannot be fooled by the command echo.
tmux -L "$sock" send-keys -t "$pane" C-u
tmux -L "$sock" send-keys -t "$pane" "printf '%s\\n' '$marker'" Enter

deadline=$(( SECONDS + timeout_s ))
until tmux -L "$sock" capture-pane -p -t "$pane" 2>/dev/null \
  | grep -qxF -- "$marker"; do
  if (( SECONDS >= deadline )); then
    echo "shell in $sock:$pane not ready after ${timeout_s}s" >&2
    exit 1
  fi
  sleep 0.1
done

# Optional: scrub the marker lines from the pane so later capture-pane
# calls do not see them. Harmless to skip.
tmux -L "$sock" send-keys -t "$pane" C-l
exit 0
