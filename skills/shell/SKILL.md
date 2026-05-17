---
name: shell
description: >-
  Author robust, safe Bash and drive terminals reliably. Use when writing or
  reviewing shell scripts (Bash strict mode, quoting, word splitting, ShellCheck,
  error and signal handling, traps, retries, timeouts, locking, concurrency,
  idempotent and atomic operations), or when automating an interactive terminal
  (tmux send-keys race conditions, wait-for completion and exit-status capture,
  capture-pane, pipe-pane, control mode) and when to pick a more reliable
  alternative instead (direct exec, setsid, systemd-run, script, expect, GNU
  screen, zellij). Triggers on bash, sh, shell script, shebang, set -euo
  pipefail, ShellCheck, heredoc, trap, tmux, screen, terminal multiplexer,
  send-keys, PTY, expect, detached background job.
license: Apache-2.0
metadata:
  lane: shell
  version: 1.0.0
  triggers: >-
    bash, shell, sh, shell script, shebang, strict mode, set -euo pipefail,
    shellcheck, quoting, word splitting, heredoc, trap, signal, retry, timeout,
    flock, tmux, screen, zellij, terminal multiplexer, send-keys, pty, tty,
    expect, detached job, background process, nohup, setsid, systemd-run
---

# Shell: robust Bash and reliable terminal automation

Two distinct jobs share this skill because they are usually done together and
fail for the same reason (treating the shell as forgiving when it is not):

1. Writing Bash that is safe under failure, untrusted input, and odd filenames.
2. Driving a live terminal (a REPL, a TUI, an SSH prompt) when no API exists.

The governing rule for job 2: **the most reliable terminal automation is the
one that never allocates a PTY.** Reach for `tmux` only after the decision
ladder below rules out the cheaper, deterministic options.

## When to use

Use this skill when the task involves any of:

- Writing, reviewing, hardening, or debugging a shell script.
- Choosing a shebang, strict-mode flags, or quoting and deciding whether Bash
  is even the right language.
- Running a long job that must survive disconnect, or capturing a command's
  output and exit status programmatically.
- Automating an interactive program that has no library or API: a REPL, an
  installer that prompts, an SSH session, a curses or full-screen TUI.
- Diagnosing flaky automation: a command "sometimes" not arriving, output
  truncated, exit status lost, a script that "succeeds" while broken.

If a real API, SDK, or library exists for the target, use that instead. Driving
its CLI through a terminal is a last resort, not a default.

## The decision ladder (read before automating a terminal)

Pick the **highest** rung that satisfies the requirement. Each rung down adds a
failure mode (a PTY, a shell, timing, screen scraping).

| Rung | Situation | Mechanism |
|------|-----------|-----------|
| 1 | Non-interactive, deterministic, finishes in foreground | Run it directly. Capture `rc=$?`, redirect stdout/stderr to files. No multiplexer. |
| 2 | Long-running, must outlive the parent or an SSH disconnect, no TTY needed | `setsid`/`nohup`, or `systemd-run --user` (cgroup-tracked). Write a log file and an **exit-code sentinel file**; poll the sentinel. |
| 3 | Strict prompt then response (login, passphrase, `(yes/no)`) | `expect` (or `pexpect`). Deterministic match, no sleeps. |
| 4 | Needs a real TTY: a REPL, a curses TUI, a program that buffers or colours differently on a pipe, or a session you also want to attach to | `tmux` on a **private socket**, with the sentinel + `wait-for` pattern below. Use control mode (`-CC`) when you must machine-parse. |

Rungs 1 to 3 are covered in `references/terminal-automation-alternatives.md`,
including `systemd-run`, `script(1)`, `expect`, GNU `screen`, and `zellij`,
with the reliability trade-off for each. Do not skip to rung 4 by habit; it is
the least reliable rung and the one this skill spends the most words making
safe.

## Non-negotiable Bash safety baseline

Every script this skill produces starts from this header. The rationale, the
real criticisms of each flag, and the mitigations are in
`references/bash-safety-and-pitfalls.md`; the rules below are the short form.

```bash
#!/usr/bin/env bash
# Strict mode. Know what each flag does NOT cover (see reference).
set -Eeuo pipefail
IFS=$'\n\t'
```

- `-e` (errexit): exit on an unhandled non-zero. It is full of exceptions
  (the left side of `&&`, any command in a condition, a function called in an
  `if`). It is a safety net, not a guarantee. Never rely on it as your only
  error handling.
- `-E` (errtrace): make an `ERR` trap fire inside functions, command
  substitutions, and subshells. Without it, `set -e` plus a trap silently
  misses errors. Always pair `-E` with an `ERR` trap.
- `-u` (nounset): unset variable is an error. Write `"${VAR:-default}"`
  deliberately; for arrays that may be empty under older Bash use
  `"${arr[@]:-}"`.
- `-o pipefail`: a pipeline fails if **any** stage fails, not just the last.
  Required, but it means a deliberately short read (a closed pipe giving
  `SIGPIPE`/141) now counts as failure: handle those cases explicitly.
- `IFS=$'\n\t'`: stop splitting unquoted expansions on spaces. It reduces
  damage from a missed quote; it does not replace quoting.

The five rules that prevent the majority of shell bugs:

1. **Quote every expansion**: `"$var"`, `"$@"`, `"${arr[@]}"`,
   `"$(cmd)"`. Unquoted means word-split then glob. The exception is a
   deliberate, commented split.
2. **Never parse `ls`. Never `for x in $(...)`.** Glob directly
   (`for f in ./*.log`) or stream NUL-delimited
   (`find . -print0 | while IFS= read -r -d '' f`).
3. **`[[ ... ]]` for tests, `(( ... ))` for arithmetic.** Inside `[[ ]]`,
   `<` and `>` are string comparison; use `(( a > b ))` for numbers.
4. **Check what can fail**: `cd "$d" || exit 1`. A pipe runs its stages in
   subshells, so `cmd | while read ...; ((n++)); done` cannot export `n`;
   use `while ...; done < <(cmd)` (process substitution) instead.
5. **Run ShellCheck.** It catches most of the above mechanically. Treat
   warnings as errors; justify each `# shellcheck disable=SCxxxx` with a
   reason on the same comment.

The canonical wrong/right table (the high-frequency pitfalls from Greg's
Wiki, condensed) is in `references/bash-safety-and-pitfalls.md`. Read it
before reviewing anyone's shell, including your own.

## Robust script architecture (the short version)

For anything past a throwaway one-liner, follow the structure in
`references/bash-robust-scripting.md` and start from
`assets/bash-script-skeleton.sh`. The load-bearing parts:

- **Cleanup is a trap, not a final line.** `trap cleanup EXIT` runs on
  success, error, and signal. Make `cleanup` idempotent. Create scratch
  space with `mktemp -d` and remove it in the trap, never with a bare
  `rm -rf "$dir/"*` where `$dir` could be empty.
- **An `ERR` trap reports where it died**: log the failing command, line,
  and exit code to stderr, then exit. This is why `-E` is in the header.
- **Logs and diagnostics go to stderr; only the script's actual result
  goes to stdout.** That keeps a script composable in a pipeline. Timestamp
  log lines.
- **Make state-changing scripts idempotent**, and write files atomically
  (write to a temp file on the same filesystem, then `mv` into place;
  `mv` is atomic, a half-written `>` redirect is not).
- **Retries**: only retry idempotent operations, with exponential backoff
  **plus jitter** and a cap, and a distinct exit code on give-up. The exact
  loop (including network-only retry classification) is in the reference.
- **Timeouts**: wrap anything that can hang in `timeout`; handle the `124`
  exit code. Bound every wait, including `wait-for` (see below).
- **Concurrency**: a bounded job pool (`xargs -P`, GNU `parallel`, or a
  `wait -n` pool) beats unbounded `&`. Serialize across script invocations
  with `flock` on a lock file, not an ad-hoc lock directory.

## Reliable tmux automation

Use only at rung 4. Full treatment, gotchas, and control mode are in
`references/tmux-automation.md`. The patterns below are the ones that turn
flaky tmux scripting into reliable scripting.

### Always use a private socket and stable IDs

A shared default server mixes your automation with the user's sessions and
their config. Isolate with `-L`, and never address windows or panes by a
name a human might also have; capture the **server-assigned ID** at creation
with `-P -F`.

```bash
sock="auto-$$"
tmux -L "$sock" new-session -d -s work -x 220 -y 50
# Capture the real pane id (%N). Do not guess "work.0".
pane=$(tmux -L "$sock" display-message -p -t work '#{pane_id}')
```

### The send-keys race, and the only two correct fixes

`send-keys` injects keystrokes into a pane; it returns immediately and does
**not** wait for a shell to be ready to read them. If the pane's shell is
still sourcing `rc` files (oh-my-zsh, nvm, pyenv, starship, direnv can take
1 to 2+ seconds), early keystrokes are silently lost or mangled. A fixed
`sleep` is a guess that fails under load. The two correct fixes:

**A. Do not use a shell at all.** Run the program as the pane's process so
there is no `rc` phase and no prompt to race:

```bash
tmux -L "$sock" new-session -d -s work "python3 -i"
```

**B. Poll for readiness with a sentinel before sending the real input**
(see `assets/shell-readiness-wait.sh`): send `printf 'SENTINEL\n'`, then
`capture-pane` in a bounded loop until the echoed sentinel appears, then
send the real command.

### Capture completion AND exit status (the core pattern)

`send-keys` discards the command's result. To run a command, block until it
finishes, and recover its exit code, append a `wait-for` signal that carries
the status. This is the single most important tmux pattern; the packaged,
timeout-bounded version is `assets/tmux-run-capture.sh`.

```bash
ch="done-$$"
tmux -L "$sock" send-keys -t "$pane" \
  'mycmd --flag; tmux -L '"$sock"' wait-for -S '"$ch"'-$?' Enter
# Block until the pane signals. ALWAYS bound it; wait-for has no native timeout.
timeout 600 tmux -L "$sock" wait-for "$ch-0" \
  || { echo "command failed or timed out" >&2; }
out=$(tmux -L "$sock" capture-pane -p -t "$pane")
```

Notes that matter:

- `wait-for` has no built-in timeout; an unsignaled channel hangs forever.
  Always wrap it in `timeout` and decide what a timeout means.
- Encode `$?` into the channel name (or write it to a file the poller
  reads). Screen-scraping a prompt for "the last exit code" is unreliable
  across shells, prompts, and locales.
- `capture-pane` sees only the visible region unless you pass a scrollback
  range (`-S -`); a long command's early output may have scrolled away. For
  unbounded or streaming output, `pipe-pane` to a file and read the file.
- Send literal text with `send-keys -l` and terminate option parsing with
  `--` so text starting with `-` is not read as a flag. `Enter` is a
  key name; a literal newline is not the same thing.

### Machine parsing: prefer control mode

If the goal is to parse tmux output programmatically rather than to present
a session to a human, control mode (`tmux -CC` / `-C`) emits structured,
line-oriented `%begin`/`%end`/`%output` blocks instead of you scraping a
rendered screen. It is markedly more reliable than `capture-pane` polling.
Details and a parser sketch are in `references/tmux-automation.md`.

### Clean up the server

A private-socket server outlives your script unless you stop it. End with
`tmux -L "$sock" kill-server 2>/dev/null || true`, ideally from the `EXIT`
trap so a crash still tears it down.

## tmux vs the alternatives (one-line guidance)

- Just need the output and exit code, no TTY: **do not use tmux** (rung 1).
- Need it to survive disconnect, no TTY: **`systemd-run --user`** or
  `setsid` + sentinel file. More reliable than a detached tmux session you
  later scrape.
- Prompt/response only: **`expect`**. Deterministic; no polling.
- Need a TTY and/or human attach later: **tmux** with the patterns above.
- Pure session recording for humans: **`script -c`** or `asciinema`.

The full matrix, with why each is more reliable for its niche, is in
`references/terminal-automation-alternatives.md`.

## Bundled resources

References (load on demand; do not inline their full contents):

- `references/bash-safety-and-pitfalls.md`: strict mode dissected with its
  real criticisms and mitigations, the canonical wrong/right pitfalls table,
  quoting and word-splitting, `[[ ]]`/`(( ))`, ShellCheck usage and
  directives.
- `references/bash-robust-scripting.md`: production script architecture,
  stderr logging, `getopts` and long options, `mktemp` and trap-based
  cleanup, idempotency, atomic writes, retry with backoff and jitter,
  timeouts, `flock`, bounded concurrency, dependency preflight, dry-run.
- `references/tmux-automation.md`: full tmux scripting model, IDs and
  formats, the race condition in depth, `wait-for`, `capture-pane` vs
  `pipe-pane`, control mode with a parser sketch, hooks, environment,
  hardening, gotchas.
- `references/terminal-automation-alternatives.md`: the decision matrix and
  deep-dive on direct exec, `setsid`/`nohup`/`disown`, `systemd-run`,
  `script(1)`, `asciinema`, `expect`/`pexpect`, GNU `screen`, `zellij`,
  `abduco`+`dvtm`, `mosh`, and SSH non-interactive options.
- `references/quick-reference.md`: dense cheat tables (parameter expansion,
  conditionals, redirection, signals, tmux verbs and format variables).

Assets (copy and adapt; they embody every rule above and are ShellCheck
clean):

- `assets/bash-script-skeleton.sh`: production template (strict mode, `ERR`
  and `EXIT` traps, leveled stderr logging, `usage`, `getopts`, `mktemp`
  cleanup, `main "$@"`).
- `assets/tmux-run-capture.sh`: run a command in an isolated tmux session,
  block on `wait-for` with a real timeout, recover the true exit code,
  capture full output, tear the server down.
- `assets/shell-readiness-wait.sh`: defeat the send-keys race by polling a
  sentinel until the shell is proven ready.
