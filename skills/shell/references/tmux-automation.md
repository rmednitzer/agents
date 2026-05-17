# tmux automation (rung 4: when a PTY is unavoidable)

tmux is the **last** rung of the decision ladder, used only when the
target genuinely needs a TTY (a REPL, a curses or full-screen TUI, a tool
that buffers or colours differently off a pipe) or when a human will also
attach. For non-interactive work prefer rungs 1 to 3
(`references/terminal-automation-alternatives.md`); for remote work see
`references/ssh-in-depth.md`. This file is the compact, correct core, not
an exhaustive tmux manual.

## Model and isolation

One server per socket; it owns sessions, windows, panes. Automation runs
**detached** and must not share the user's server or read their config:

```bash
sock="auto-$$"
tmux -L "$sock" -f /dev/null new-session -d -s work -x 220 -y 50
pane=$(tmux -L "$sock" display-message -p -t work '#{pane_id}')
```

- `-L NAME` (or `-S PATH`): a private server, so `kill-server` cannot
  destroy the user's sessions and a stray automation session cannot
  pollute theirs. Keep `-L` sockets in the per-user default directory,
  not a world-writable path.
- `-f /dev/null`: the user's `~/.tmux.conf` (custom prefix, mouse mode,
  hooks) cannot change behaviour under you.
- Address objects by the **server-assigned id** captured at creation
  (`#{pane_id}` like `%3`), never `work.0` (breaks on layout change).

## The send-keys race, and the only two correct fixes

`send-keys` injects keystrokes and returns immediately; it does not wait
for a shell to be ready. If the pane's shell is still sourcing rc files
(oh-my-zsh, nvm, pyenv, mise, direnv, starship: 1 to 2+ seconds), early
keystrokes are lost or mangled. A fixed `sleep` is a guess that fails
under load. Correct fixes:

1. **No shell at all.** Run the program as the pane process so there is
   no rc phase or prompt to race: `tmux -L "$sock" new-session -d
   'exec python3 -i'`. Prefer this whenever the program is known.
2. **Poll a sentinel** (see `assets/shell-readiness-wait.sh`): send
   `printf '%s\n' MARKER`, then `capture-pane` in a **bounded** loop
   until a whole-line match on `MARKER` appears, then send real input.

## Completion and exit status (the core pattern)

`send-keys` discards the command's result. Recover completion and the
true exit code with a **fixed** `wait-for` channel plus `$?` written to a
file. The channel name must be known in advance to wait on it, so it
cannot encode `$?` (you would only wake for the one value you guessed,
and a failing command would block until the timeout). Packaged with a
timeout in `assets/tmux-run-capture.sh`:

```bash
ch="done-$$"
rcfile=$(mktemp)
tmux -L "$sock" send-keys -t "$pane" \
  "mycmd --flag; echo \$? > $rcfile; tmux -L $sock wait-for -S $ch" Enter

# No leading '!': with `if ! cmd; then`, $? in the branch is the negated
# status (0), not timeout's real one. Capture via `|| st=$?` instead.
st=0
timeout 600 tmux -L "$sock" wait-for "$ch" || st=$?
(( st == 124 )) && echo "wait timed out before completion" >&2
status=$(< "$rcfile")                                   # true exit code
out=$(tmux -L "$sock" capture-pane -p -S - -t "$pane")  # full scrollback
```

Why: `wait-for` has no native timeout, so it is always wrapped in
`timeout`. A fixed channel is signalled for every exit status. The file
carries the exact code; scraping a prompt for `$?` is shell, theme, and
locale dependent and races the next prompt.

## Capturing output

- `capture-pane -p -S - -t T`: snapshot including scrollback. Plain
  `capture-pane` returns only the visible region, so a long command's
  early output may already have scrolled away. It includes the prompt
  and your sentinel lines (filter them).
- `pipe-pane -o -t T 'cat >> file'`: tee everything the pane emits for
  its lifetime. The reliable way to get **complete, streaming** output;
  read the file, do not poll the screen. `-o` toggles, so call once.

## Sending input safely

- Key names (`Enter`, `C-c`, `Escape`) are interpreted; other arguments
  are literal. `Enter`/`C-m` send a carriage return; a literal `\n` is
  not the same as pressing Enter.
- `send-keys -l -- "$text"`: `-l` disables key-name interpretation;
  `--` ends option parsing so text starting with `-` is not a flag. Use
  for arbitrary or untrusted strings. `C-u` first clears a half-typed
  line; large payloads go via `load-buffer` + `paste-buffer`.

## Machine parsing: prefer control mode

If no human will see the session and you only want structured results,
do not scrape a rendered screen. Control mode (`tmux -C` / `-CC`) makes
tmux a line protocol with explicit `%begin`/`%end`/`%error`/`%output`
framing per command, which is markedly more reliable than `capture-pane`
polling. Treat all `%output` data as untrusted bytes and parse
defensively.

## Cleanup

The private server outlives the script unless stopped. Tear it down from
an `EXIT` trap so a crash still cleans up:

```bash
trap 'tmux -L "$sock" kill-server 2>/dev/null || true' EXIT
```

Idempotent start: `tmux -L "$sock" has-session -t work 2>/dev/null ||
tmux -L "$sock" new-session -d -s work`.

## Gotchas

- Default socket instead of `-L`: collides with the user; `kill-server`
  is catastrophic.
- `name.0` instead of the captured `%id`: breaks on layout change.
- Fixed `sleep` before `send-keys`: flaky under load. Sentinel poll or
  run the program as the pane process.
- `wait-for` without `timeout`: permanent hang on any failure path.
- `$?` encoded in the channel name and waited on `$ch-0`: a non-zero
  exit signals a name nobody waits on, so it hangs to the timeout. Use a
  fixed channel and an rc file.
- `if ! timeout ...; then ... $? ...`: `$?` is the negated status (0).
  Capture with `... || rc=$?` before branching.
- Plain `capture-pane` for long output: early lines already scrolled.
  Use `-S -` or `pipe-pane`.
- String starting with `-` sent without `-l --`: read as options.
- Server left running: leaked processes and sockets. Kill it in the
  `EXIT` trap.
