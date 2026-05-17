# Reliable tmux automation

The full model behind the tmux section of `SKILL.md`. Use only at rung 4
of the decision ladder (a real TTY is required, or a human will attach).
For everything else see `references/terminal-automation-alternatives.md`.

## 1. The model: client, server, session, window, pane

One `tmux` server per socket. It owns sessions; a session has windows; a
window has panes; a pane runs one process. A client attaches a terminal to
a session. Automation should run **detached** (no client) and address
objects by **server-assigned IDs**, not by names a human might collide
with.

IDs and their sigils, stable for the life of the object:

- session: `$N`, format `#{session_id}`
- window: `@N`, format `#{window_id}`
- pane: `%N`, format `#{pane_id}`

A target like `work.0` ("session work, window 0, pane 0") is a guess that
breaks the moment layout changes. Capture the real ID at creation:

```bash
sock="auto-$$"
tmux -L "$sock" new-session -d -s work -x 220 -y 50 \
  -P -F '#{session_id} #{window_id} #{pane_id}'
# e.g. prints: $0 @0 %0  -> parse and keep %0
```

`-P` prints information about the new object; `-F` chooses the format.
`display-message -p -t TARGET '#{pane_id}'` retrieves it later.

## 2. Isolation: always a private socket

`-L NAME` uses a named socket under the tmux socket directory; `-S PATH`
uses an explicit path. Either way, automation gets its **own server**,
separate from the user's sessions and, importantly, separate from their
`~/.tmux.conf` if you also pass `-f /dev/null`:

```bash
tmux -L "auto-$$" -f /dev/null new-session -d -s work
```

Reasons this is not optional for automation:

- The user's config can rebind keys, set a non-default prefix, enable
  `set -g mouse`, or add hooks that interfere with scripted input.
- `kill-server` on a shared socket would destroy the user's work.
- A leftover automation session cannot pollute the user's session list.

## 3. The send-keys race condition (in depth)

`send-keys` writes bytes into the pane's input as if typed, then returns
**immediately**. It does not know or care whether anything is ready to
read them. New pane, new shell: the shell is sourcing `/etc/profile`,
`~/.bashrc` or `~/.zshrc`, and frameworks (oh-my-zsh, nvm, pyenv, rbenv,
mise, direnv, starship) that can take 1 to 2+ seconds, longer on a loaded
box. Keystrokes sent during that window are buffered into the terminal
line discipline and then often discarded or merged when the shell finally
takes over the TTY, so the command silently does not run, or runs
truncated.

A fixed `sleep 2` is a bet against the worst case and loses under load or
on a cold cache. There are exactly two robust fixes.

### Fix A: no shell, no race

Run the target program as the pane's process. There is no `rc` phase and
no prompt to beat:

```bash
tmux -L "$sock" new-session -d -s work 'exec python3 -i'
tmux -L "$sock" new-window  -t work   'exec psql "$DATABASE_URL"'
```

Prefer this whenever you know the program up front. `exec` replaces the
shell so a stray prompt cannot appear.

### Fix B: prove readiness with a sentinel

When you genuinely need a shell, do not time it: detect it. Send a
sentinel and wait until its echo appears, then send real input. Packaged
in `assets/shell-readiness-wait.sh`; the core:

```bash
marker="__READY_$$__"
tmux -L "$sock" send-keys -t "$pane" "printf '%s\\n' $marker" Enter
deadline=$(( SECONDS + 15 ))
until tmux -L "$sock" capture-pane -p -t "$pane" | grep -q "^$marker$"; do
  (( SECONDS > deadline )) && { echo "shell never became ready" >&2; exit 1; }
  sleep 0.1
done
```

Note the loop is **bounded**. Every wait in tmux automation has a
deadline; an unbounded poll is a hang waiting to happen.

## 4. The core pattern: completion plus exit status

`send-keys` throws away the command's result: you get neither "it
finished" nor "it succeeded". Recover both by appending a `wait-for`
signal whose channel name carries `$?`. `wait-for -S CH` wakes any waiter
on channel `CH`; `wait-for CH` blocks until signaled. Packaged with a real
timeout in `assets/tmux-run-capture.sh`. The mechanism:

```bash
ch="done-$$"
# Append: on completion, signal a channel whose name encodes the exit code.
tmux -L "$sock" send-keys -t "$pane" \
  "mycmd --flag; tmux -L $sock wait-for -S ${ch}-\$?" Enter

# wait-for has NO native timeout; an unsignaled channel blocks forever.
if timeout 600 tmux -L "$sock" wait-for "${ch}-0"; then
  status=0
else
  status=$?           # 124 = our timeout; otherwise the command's nonzero
fi
out=$(tmux -L "$sock" capture-pane -p -S - -t "$pane")
```

Why each choice:

- **Exit code in the channel name**, not scraped from a prompt. A prompt's
  `$?` rendering depends on the shell, `PROMPT_COMMAND`, theme, and locale,
  and races with the next prompt. The channel name is exact. (An equally
  robust variant: `echo $? > rcfile` and read the file.)
- **`timeout` around `wait-for`** because tmux provides no timeout for it;
  this is the single most common tmux-automation hang.
- **`capture-pane -S -`** to include scrollback. Plain `capture-pane`
  returns only the visible region, so early output of a long command is
  already gone. For large or streaming output use `pipe-pane` (next
  section), not a bigger capture.

## 5. Capturing output: capture-pane vs pipe-pane

- `capture-pane -p [-S start] [-E end] -t TARGET`: snapshot of the pane
  buffer to stdout. `-S -` means "from the start of history"; `-S -3000`
  the last 3000 lines. Good for "what is on screen now". It is a snapshot,
  so it can miss data that scrolled past history, and it includes the
  prompt and your sentinel lines (filter them).
- `pipe-pane -o -t TARGET 'cat >> /path/log'`: tee everything the pane
  outputs to a command, for the life of the pane. This is the reliable way
  to get **complete, streaming** output. `-o` toggles, so call it once.
  Read the file; do not poll the screen.

```bash
log=$(mktemp)
tmux -L "$sock" pipe-pane -o -t "$pane" "cat >> '$log'"
# ... run command, wait-for ...
tmux -L "$sock" pipe-pane -t "$pane"      # stop piping
```

## 6. Sending input safely

- `send-keys -t T 'literal text' Enter`: arguments that match key names
  (`Enter`, `C-c`, `Escape`, `Up`) are sent as those keys; everything else
  is sent literally. `Enter` and `C-m` send a carriage return; a literal
  `\n` in a single argument is not the same as pressing Enter.
- `send-keys -l -- "$text"`: `-l` disables key-name interpretation (send
  exactly these bytes); `--` ends option parsing so text starting with `-`
  is not read as a flag. Use this for arbitrary or untrusted strings.
- Control and meta: `C-c` (SIGINT to the foreground process), `C-d` (EOF),
  `C-u` (clear the input line, useful to flush a half-typed line before
  sending a fresh command).
- Paste-style input: load a buffer and `paste-buffer` rather than
  send-keys for large or multi-line payloads; it avoids per-key timing and
  bracketed-paste surprises.

```bash
tmux -L "$sock" send-keys -t "$pane" C-u            # clear pending line
printf '%s' "$payload" | tmux -L "$sock" load-buffer -
tmux -L "$sock" paste-buffer -d -t "$pane"          # -d deletes the buffer after
```

## 7. Control mode: the right tool for machine parsing

If a human will never look at this session and you only want structured
results, do not scrape a rendered screen at all. Control mode makes tmux a
line protocol: send commands on stdin, receive `%begin` / `%end` /
`%error` / `%output` / `%exit` notifications on stdout.

- `tmux -CC` (attach control mode, used by terminals like iTerm2).
- `tmux -C new-session -d ...` for a pure programmatic client: each
  command's reply is delimited by `%begin <t> <num> <flags>` and
  `%end`/`%error <t> <num> <flags>`, and asynchronous pane output arrives
  as `%output %PANE data`.

A minimal parser contract: write a command line, read lines until the
matching `%end`/`%error` for that command number, treat `%error` as
failure, and demultiplex `%output` by pane id. This is dramatically more
reliable than `capture-pane` polling because framing is explicit and you
never guess where output starts or whether it scrolled away. Treat all
fields as untrusted text and parse defensively (data after `%output` can
contain anything, including lines that look like notifications).

## 8. Hooks and waiting on events

`set-hook` runs a tmux command when an event fires. The automation-relevant
one is reacting to a pane's process exiting:

```bash
tmux -L "$sock" set-hook -t "$pane" pane-died \
  "run-shell 'tmux -L $sock wait-for -S exited'"
tmux -L "$sock" set -t "$pane" remain-on-exit on   # keep the pane to read it
timeout 600 tmux -L "$sock" wait-for exited
```

`remain-on-exit on` keeps a dead pane (and its final output) instead of
destroying it, so a fast-failing command's output is still capturable.
`run-shell` executes a shell command from inside tmux and is the usual
bridge from a hook to a `wait-for` signal.

## 9. Environment and working directory

A detached server captured the environment at **server start**; later
sessions inherit a snapshot, not your current shell. Set what you need
explicitly rather than assuming inheritance:

```bash
tmux -L "$sock" new-session -d -s work -c "$PWD" -e "FOO=$foo" -e "PATH=$PATH"
tmux -L "$sock" set-environment -t work BAR baz   # for sessions started later
```

`-c` sets the pane's start directory; `-e` sets an environment variable
for that session. Do not rely on the server having your `PATH`.

## 10. Hardening and cleanup

- `-f /dev/null` so the user's `~/.tmux.conf` cannot change behavior under
  you (custom prefix, mouse mode, hooks, `default-command`).
- The socket is created with the invoking user's permissions; do not place
  an `-S` socket in a world-writable directory where another user could
  pre-create or hijack it. `-L` (default socket dir, per-user) is safest.
- Treat captured pane content as untrusted: it can contain escape
  sequences and arbitrary bytes. Sanitize before logging to a terminal or
  embedding in another command. Never `eval` it.
- The server outlives your script. Tear it down, ideally from the script's
  `EXIT` trap so a crash still cleans up:

  ```bash
  cleanup() { tmux -L "$sock" kill-server 2>/dev/null || true; }
  trap cleanup EXIT
  ```

- Idempotent start: `tmux -L "$sock" has-session -t work 2>/dev/null ||
  tmux -L "$sock" new-session -d -s work` so re-running does not error or
  duplicate.

## 11. Gotchas checklist

- Addressed a pane by `name.0` instead of the captured `%id`: breaks on
  layout change. Capture and use `#{pane_id}`.
- Used the default socket: collided with the user; `kill-server` would be
  catastrophic. Use `-L`.
- Fixed `sleep` before `send-keys`: flaky under load. Poll a sentinel or
  run the program as the pane process.
- `wait-for` without `timeout`: permanent hang on any failure path. Always
  bound it.
- Scraped the prompt for the exit code: wrong across shells/themes/locales.
  Encode `$?` in the channel name or a file.
- Plain `capture-pane` for long output: early lines already scrolled past.
  Use `-S -` for a bounded grab or `pipe-pane` for complete output.
- Sent a string starting with `-` without `-l --`: read as options.
- Left the server running: leaked processes and sockets. Kill it in the
  `EXIT` trap.
