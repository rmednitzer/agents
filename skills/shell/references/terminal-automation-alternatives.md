# Terminal automation: tmux and the more reliable alternatives

tmux is one tool on a ladder. For most automation goals something simpler
is more reliable because it removes a failure mode (a PTY, a shell, timing,
screen scraping). This is the deep version of the decision ladder in
`SKILL.md`. Choose the simplest mechanism that meets the requirement.

## Decision matrix

| Requirement | Use | Why it beats tmux here |
|---|---|---|
| Run, get stdout/stderr and exit code, finishes in foreground | Direct exec with redirection | No PTY, no shell, no scraping. The exit code is just `$?`. |
| Must outlive the parent / SSH disconnect; no TTY needed | `systemd-run --user` (best) or `setsid` + sentinel file | Process is tracked (cgroup or PID file) and the real exit code is recorded, not scraped. |
| Deterministic prompt then response (passphrase, `(yes/no)`) | `expect` / `pexpect` | Matches on expected output; no `sleep`, no race. |
| Needs a real TTY (REPL, curses TUI, color/buffering differs on a pipe), or a human will attach | tmux (see `tmux-automation.md`) | The only rung that supplies an interactive PTY a human can also join. |
| Record a human session for later viewing | `script -c` / `asciinema` | Purpose-built capture; no automation logic to get wrong. |
| Resilient interactive shell over flaky network | `mosh` (+ tmux inside) | Survives roaming and latency; tmux alone does not. |

## 1. Direct execution (rung 1, the default)

If the program is non-interactive and finishes while you wait, a
multiplexer adds only failure modes. Run it; capture everything:

```bash
if out=$(mycmd --flag 2>err.log); then
  : # success; $out has stdout, err.log has stderr
else
  rc=$?; echo "failed rc=$rc" >&2; cat err.log >&2
fi
```

Need stdout, stderr, and exit code separately and reliably, no shell
involved: `mapfile`/files plus `"$?"`. Need a PTY only because the program
checks `isatty()` (it withholds color or line-buffers when piped) but is
otherwise non-interactive: allocate just a PTY without a multiplexer with
`script -qec 'mycmd' /dev/null` (portable) or `unbuffer mycmd` (from
`expect`). That is far less machinery than tmux.

## 2. Detached and durable, no TTY (rung 2)

The goal is "start it, let it outlive me, learn how it ended later". A
detached tmux session can do this but then you are back to scraping a pane
for completion and exit status. Two better options:

### `setsid` / `nohup` / `disown` plus a sentinel file

Detach from the controlling terminal and record the result yourself, so
completion and exit status are facts on disk, not screen text:

```bash
job=/var/tmp/job.$$            # log + status live here
setsid bash -c '
  { mycmd --flag; echo $? > "'"$job"'.rc"; } > "'"$job"'.log" 2>&1
' < /dev/null &
# later, from anywhere:
[[ -f $job.rc ]] && echo "exited $(cat "$job.rc")"
```

`setsid` gives a new session so the job is not killed when the parent or
SSH connection dies. `nohup` only ignores `SIGHUP` (weaker); `disown -h`
removes a job from the shell's table after the fact. Prefer `setsid` for
new work.

### `systemd-run --user` (the most reliable detached option)

If systemd is present, this is the strongest choice: the job runs in its
own transient unit and cgroup, fully decoupled from your shell, with the
exit status, logs, and resource accounting recorded by the service
manager, not by you.

```bash
systemd-run --user --unit="job-$$" --collect \
  --property=Type=oneshot \
  /usr/bin/env bash -c 'mycmd --flag'
# observe without scraping anything:
systemctl --user status "job-$$"
journalctl --user -u "job-$$" --no-pager
# exit code, exactly, from the manager:
systemctl --user show -p ExecMainStatus --value "job-$$"
```

Add `--property=RuntimeMaxSec=600` for a hard timeout, `--scope` to run
synchronously in the foreground but still cgroup-tracked, or `--pty` for an
interactive transient unit. This removes essentially every tmux failure
mode for the durable-job use case.

## 3. Deterministic prompt/response (rung 3): expect

When the interaction is "wait for known output, send a known response"
(SSH host-key prompt, a passphrase, an installer's `Proceed? [y/N]`),
`expect` is more reliable than tmux send-keys because it **synchronizes on
the output it is waiting for** instead of timing input blindly. No
`sleep`, no readiness poll, no scrape.

```expect
#!/usr/bin/env expect
set timeout 30
spawn ssh user@host
expect {
  "yes/no" { send "yes\r"; exp_continue }
  "assword:" { send "$env(PW)\r" }
  timeout { puts stderr "no prompt in time"; exit 1 }
}
expect "$ "
send "uptime\r"
expect eof
catch wait result
exit [lindex $result 3]   ;# real child exit status
```

Every `expect` has an explicit `timeout`; `catch wait` yields the child's
true exit status. In Python, `pexpect` is the same model with better
string handling. Use this, not tmux, for credential and confirmation
flows.

## 4. Session recording for humans: script and asciinema

If the deliverable is a recording a person will watch, not a result a
program will parse, use a recorder. `script` (util-linux) with `-c` runs
one command in a PTY and logs the typescript; `--timing` (or
`scriptreplay`) preserves timing. `asciinema rec` produces a compact,
replayable cast. Both are more reliable than driving tmux and screen
scraping because there is no automation logic that can be wrong: you are
recording, not steering.

## 5. Other multiplexers and when they help

- **GNU `screen`**: the older multiplexer. Scriptable detached
  (`screen -dmS name cmd`) and `screen -S name -X stuff $'cmd\r'` is the
  send-keys analogue. It has the same race and same lack of native
  completion/exit-status signaling as tmux (no `wait-for` equivalent), so
  it is not more reliable, only more widely preinstalled on old systems.
  Prefer tmux when both exist.
- **`zellij`**: modern multiplexer with a typed action API
  (`zellij action ...`) and a layout/plugin system. Cleaner for building a
  fixed interactive environment; for headless "run and capture exit code"
  it still does not beat rung 1 to 3. Good when a human will use the
  layout.
- **`abduco` + `dvtm`**: `abduco` is a tiny, robust session detach/attach
  tool; `dvtm` adds tiling. `abduco -n name cmd` for pure
  detach/reattach is smaller and arguably more robust than tmux when you do
  not need windows, panes, or scripting features. It has no `wait-for`
  either, so pair it with the sentinel-file pattern from section 2.
- **`mosh`**: not a multiplexer; a roaming-resilient remote shell. It does
  not replace tmux; it complements it (run tmux inside mosh) when the value
  is surviving a flaky or roaming network connection.

## 6. SSH without a pseudo-terminal

When the "terminal" is only there because you used `ssh`, drop the
terminal. `ssh host cmd` (no `-t`) runs `cmd` without a PTY and returns
its **real exit status** directly: no scraping, no prompt parsing.

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 host 'systemctl is-active nginx'
echo $?    # the remote command's exit code, exactly
```

Do not put `--` after the host: `ssh` stops parsing its own options at the
destination, so everything after it (including `--`) is the **remote**
command. `ssh host -- cmd` asks the remote shell to run `-- cmd`. To guard
a hostname that could start with `-`, put the terminator before the host
(`ssh [opts] -- host cmd`).

Use `-T` to force no PTY, `-t` only when the remote genuinely needs one
(an interactive tool, `sudo` with a TTY requirement). `BatchMode=yes`
turns password and host-key prompts into immediate failures instead of
hangs, which is what you want in automation. Multiplex repeated
connections with `ControlMaster auto` + `ControlPersist` for speed and
reliability rather than scripting a single long interactive session.

## 7. The general principle

Each rung you descend (direct exec, then detached + sentinel, then expect,
then tmux, then screen scraping) adds a thing that can be flaky. Pick the
highest rung that meets the requirement, bound every wait with a timeout,
and make completion and exit status explicit facts (an exit code in a
file, a `wait-for` channel, a service manager's recorded status), never
text scraped off a rendered screen.
