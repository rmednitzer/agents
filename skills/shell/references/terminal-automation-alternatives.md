# Reliable command execution: mechanisms and trade-offs

The deep version of the decision ladder in `SKILL.md`. Every rung removes
a failure mode; the thing to avoid for automation is steering a live
screen and scraping it back, because that has no reliable completion or
exit-status signal. Choose the simplest mechanism that meets the
requirement.

## Decision matrix

| Requirement | Use | Why |
|---|---|---|
| Run, get stdout/stderr and exit code, finishes in foreground | Direct exec with redirection | No PTY, no shell, no scraping. The exit code is just `$?`. |
| Must outlive the parent / SSH disconnect; no TTY needed | `systemd-run --user` (best) or `setsid` + sentinel file | Process is tracked (cgroup or PID file) and the real exit code is recorded, not scraped. |
| Deterministic prompt then response (passphrase, `(yes/no)`) | `expect` / `pexpect` | Synchronizes on expected output; no `sleep`, no race; yields the child's true exit status. |
| Program only needs a PTY (checks `isatty()`) but is not interactive | `script -qec 'cmd' /dev/null` or `unbuffer` | Supplies a PTY with zero scripting logic. |
| A human will attach to a long-lived session | `systemd-run --pty`, GNU `screen`, `abduco` | Interactive UX, not automation; do not put automation logic on top. |
| Record a human session for later viewing | `script -c` / `asciinema` | Purpose-built capture; no automation logic to get wrong. |
| Resilient interactive shell over flaky network | `mosh` (with `screen`/`abduco` inside, for a human) | Survives roaming and latency. |

## 1. Direct execution (rung 1, the default)

If the program is non-interactive and finishes while you wait, any
terminal wrapper adds only failure modes. Run it; capture everything:

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
otherwise non-interactive: allocate just a PTY with
`script -qec 'mycmd' /dev/null` (portable) or `unbuffer mycmd` (from
`expect`). That is far less machinery, and far more reliable, than driving
a multiplexer.

## 2. Detached and durable, no TTY (rung 2)

The goal is "start it, let it outlive me, learn how it ended later". A
detached interactive session can technically do this, but then you are
back to scraping a screen for completion and exit status. Two better
options record the result as a fact instead.

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
interactive transient unit. This removes essentially every
screen-scraping failure mode for the durable-job use case.

## 3. Deterministic prompt/response (rung 3): expect

When the interaction is "wait for known output, send a known response"
(SSH host-key prompt, a passphrase, an installer's `Proceed? [y/N]`), or a
program simply refuses to run without a TTY, `expect` is the reliable
tool: it allocates a real PTY and **synchronizes on the output it is
waiting for** instead of timing input blindly. No `sleep`, no readiness
poll, no scrape.

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
string handling. This is the floor for genuinely interactive targets; use
it for credential and confirmation flows.

## 4. Session recording for humans: script and asciinema

If the deliverable is a recording a person will watch, not a result a
program will parse, use a recorder. `script` (util-linux) with `-c` runs
one command in a PTY and logs the typescript; `--timing` (or
`scriptreplay`) preserves timing. `asciinema rec` produces a compact,
replayable cast. Both are reliable here precisely because there is no
automation logic that can be wrong: you are recording, not steering.

## 5. Detach/reattach and multiplexers (interactive UX, not automation)

These exist so a **human** can leave a session running and come back to
it, or see several panes. None has a native completion or exit-status
signal, so do not build automation on them; if you need durability, use
rung 2 instead, and if you need a script-driven interaction, use rung 3.

- **GNU `screen`**: the older multiplexer. Scriptable detached
  (`screen -dmS name cmd`), and `screen -S name -X stuff $'cmd\r'` injects
  keystrokes, but with the same readiness race and no completion signal,
  so it is not a reliable automation primitive. Widely preinstalled on
  older systems.
- **`zellij`**: modern multiplexer with a typed action API
  (`zellij action ...`) and layouts. Pleasant for a fixed human
  environment; for headless "run and capture exit code" it still does not
  beat rungs 1 to 3.
- **`abduco` (+ `dvtm`)**: `abduco -n name cmd` is a tiny, robust pure
  detach/reattach tool (smaller and arguably more robust than a full
  multiplexer when you do not need panes). No completion signal, so pair
  it with the sentinel-file pattern from section 2.
- **`mosh`**: not a multiplexer; a roaming-resilient remote shell for a
  human. Run `screen`/`abduco` inside it when the value is surviving a
  flaky or roaming connection.

## 6. SSH without a pseudo-terminal

When the "terminal" is only there because you used `ssh`, drop the
terminal. `ssh host cmd` (no `-t`) runs `cmd` without a PTY and returns
its **real exit status** directly: no scraping, no prompt parsing.

```bash
ssh -n -T -o BatchMode=yes -o ConnectTimeout=10 \
  host 'systemctl is-active nginx'
echo $?    # the remote command's exit code, exactly
```

This is rung 1 over the network and is almost always the right answer for
remote automation. The full treatment (ssh_config match order, keys and
agent, host-key trust and certificate authorities, `ProxyJump`,
`ControlMaster` multiplexing, the stdin-stealing and double-quoting
pitfalls, the `--`-after-host trap, forwarding, `scp`/`rsync`,
`authorized_keys` constraints, and `sshd` hardening) is in
`references/ssh-in-depth.md`. The minimum: `BatchMode=yes` (fail fast,
never prompt), `ConnectTimeout`/`ServerAlive*` (do not hang on a dead
peer), `-n` (ssh steals stdin in `while read` loops), `-T` no PTY (`-tt`
only when the remote demands one, driven by `expect`), and never
`ssh host -- cmd` (the `--` becomes the remote command). Multiplex with
`ControlMaster auto` + `ControlPersist` rather than scripting one long
interactive session.

## 7. The general principle

Each rung you descend (direct exec, then detached + sentinel, then expect)
adds a thing that can be flaky. Pick the highest rung that meets the
requirement, bound every wait with a timeout, and make completion and exit
status explicit facts (an exit code in a file, a service manager's
recorded status), never text scraped off a rendered screen. If the only
reason a PTY is in the picture is `ssh` or an `isatty()` check, remove it
(section 6, or `script -qec`); a screen you have to scrape is not an
automation interface.
