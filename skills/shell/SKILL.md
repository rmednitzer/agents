---
name: shell
description: >-
  Author robust, safe Bash and run commands reliably on local and remote
  machines. Use when writing or reviewing shell scripts (strict mode, quoting,
  word splitting, ShellCheck, error and signal handling, traps, retries,
  timeouts, locking, concurrency, idempotent and atomic writes); when automating
  SSH (ssh_config model, keys and agent, host-key trust and certificate
  authorities, ProxyJump bastions, ControlMaster multiplexing, BatchMode,
  no-PTY exit-status semantics, scp and rsync, authorized_keys constraints,
  sshd hardening); or when choosing a reliable mechanism to run or drive a
  process instead of a fragile scripted session (direct exec, setsid,
  systemd-run, script, expect). Triggers on bash, sh, shell script, shebang,
  ShellCheck, heredoc, trap, ssh, ssh_config, scp, rsync, known_hosts,
  ProxyJump, ControlMaster, BatchMode, expect, detached background job.
license: Apache-2.0
metadata:
  lane: shell
  version: 1.2.0
  triggers: >-
    bash, shell, sh, shell script, shebang, strict mode, set -euo pipefail,
    shellcheck, quoting, word splitting, heredoc, trap, signal, retry, timeout,
    flock, ssh, scp, sftp, rsync, ssh_config, known_hosts, host key, ProxyJump,
    bastion, jump host, ControlMaster, ssh multiplexing, BatchMode, ssh
    certificate, sshd hardening, expect, pexpect, pty, tty, detached job,
    background process, nohup, setsid, systemd-run, script command
---

# Shell: robust Bash and reliable command execution

Two jobs share this skill because they are usually done together and fail for
the same reason (treating the shell as forgiving when it is not):

1. Writing Bash that is safe under failure, untrusted input, and odd filenames.
2. Running commands reliably on local and **remote** machines: SSH first (it
   is the terminal you actually automate), and otherwise the most
   deterministic mechanism, not a scripted interactive session.

The governing rule for job 2: **the most reliable automation never allocates a
PTY.** For remote work that means `ssh host cmd` with the right options, not
scripting an interactive login. Drop to a pseudo-terminal only when a program
genuinely requires one, and even then keep the interaction deterministic.

## When to use

Use this skill when the task involves any of:

- Writing, reviewing, hardening, or debugging a shell script.
- Choosing a shebang, strict-mode flags, or quoting and deciding whether Bash
  is even the right language.
- Running a long job that must survive disconnect, or capturing a command's
  output and exit status programmatically.
- Driving a program that has no API and either prompts or needs a TTY (an
  installer, a passphrase, an SSH session): choosing `expect` or `script`
  over a fragile hand-rolled session.
- Diagnosing flaky automation: a command "sometimes" not arriving, output
  truncated, exit status lost, a script that "succeeds" while broken.

If a real API, SDK, or library exists for the target, use that instead. Driving
its CLI through a terminal is a last resort, not a default.

## The decision ladder (read before automating anything)

Pick the **highest** rung that satisfies the requirement. Each rung down adds a
failure mode (a PTY, a shell, timing).

| Rung | Situation | Mechanism |
|------|-----------|-----------|
| 1 | Non-interactive, deterministic, finishes in foreground | Run it directly. Capture `rc=$?`, redirect stdout/stderr to files. No wrapper. |
| 2 | Long-running, must outlive the parent or an SSH disconnect, no TTY needed | `setsid`/`nohup`, or `systemd-run --user` (cgroup-tracked). Write a log file and an **exit-code sentinel file**; poll the sentinel. |
| 3 | Strict prompt then response, or a program that requires a TTY | `expect` (or `pexpect`): allocates a real PTY and matches deterministically, no sleeps. For a program that only checks `isatty()` but is not interactive, `script -qec 'cmd' /dev/null` or `unbuffer` supplies a PTY with no scripting at all. |

**There is no rung 4.** A long-lived session a human also attaches to is an
interactive-UX concern, not reliable automation: if you need detach and
reattach use `systemd-run --user --pty`, GNU `screen`, or `abduco` (see
`references/terminal-automation-alternatives.md`), but never build automation
logic on top of scraping a rendered screen. Screen scraping has no reliable
completion or exit-status signal; rungs 1 to 3 always do.

**Remote targets ride these same rungs over SSH, not a scripted login.**
A remote command is rung 1: `ssh host cmd` runs without a PTY and returns
the command's real exit code. Make it reliable with `BatchMode=yes`,
`ConnectTimeout`, and `ServerAliveInterval` so it fails fast instead of
hanging; see the SSH section below and `references/ssh-in-depth.md`. Use
`ssh -tt` (force a PTY) only when the remote genuinely demands one, and
drive that with `expect`, not ad-hoc sleeps.

Rungs 1 to 3 and the detach/multiplexer options are covered in
`references/terminal-automation-alternatives.md`, including `systemd-run`,
`script(1)`, `expect`, GNU `screen`, and `zellij`, with the reliability
trade-off for each. Rung 3 (`expect`) is the floor for a genuinely
interactive target; everything above it is more reliable, so do not reach
for a PTY by habit.

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
- `-o pipefail`: a pipeline's status is the last (rightmost) command to exit
  non-zero, not just the final stage. Required, but it means a deliberately
  short read (a closed pipe giving `SIGPIPE`/141) now counts as failure:
  handle those cases explicitly.
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
  exit code. Bound every wait.
- **Concurrency**: a bounded job pool (`xargs -P`, GNU `parallel`, or a
  `wait -n` pool) beats unbounded `&`. Serialize across script invocations
  with `flock` on a lock file, not an ad-hoc lock directory.

## Reliable SSH automation

SSH is the terminal you actually automate. Almost every flaky "remote"
script traces back to one of the items below. Full treatment (ssh_config
match order, keys and agent, host-key trust and CAs, jump hosts,
multiplexing, forwarding, file transfer, authorized_keys constraints,
sshd hardening) is in `references/ssh-in-depth.md`.

The non-negotiable automation invocation:

```bash
ssh -n -T \
  -o BatchMode=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=accept-new \
  "$host" 'set -Eeuo pipefail; remote-cmd --flag'
rc=$?   # the REMOTE command's real exit code (255 is ssh's own failure)
```

Why each piece, and the load-bearing rules:

- **`BatchMode=yes`**: never prompt. A missing or wrong key fails
  immediately instead of blocking CI on a hidden password prompt forever.
  Pair with key or certificate auth.
- **`ConnectTimeout` + `ServerAliveInterval`/`ServerAliveCountMax`**: a
  dead or black-holed host is detected in seconds, not never. Without
  these, automation hangs indefinitely on a firewalled peer.
- **`StrictHostKeyChecking=accept-new`**: accept a new host, still refuse
  a *changed* key. Never `no` (defeats MITM protection); never leave the
  interactive default in a script (it hangs with no TTY). Prepopulate
  with `ssh-keyscan` and use `yes` when you can.
- **`-n` (stdin from `/dev/null`)**: `ssh` reads stdin, so inside
  `while read h; do ssh "$h" ...; done < hosts` the first `ssh` eats the
  rest of `hosts` and the loop runs once. `-n` (or `< /dev/null`) unless
  you are deliberately piping data in.
- **`-T` no PTY**; the remote command's exit status passes through.
  `ssh -tt` forces a PTY only when the remote demands one (`sudo`
  `requiretty`, an interactive tool); script that case with `expect`.
- **`--` is not a local option terminator after the host.** `ssh` stops
  parsing its own options at the destination, so `ssh host -- cmd` asks
  the *remote* shell to run `-- cmd`. The guard goes before the host.
- **Remote command quoting is double expansion** (local shell, then the
  remote login shell). Single-quote to defer, or push a script:
  `ssh host bash -s -- arg < script.sh`.
- **`IdentitiesOnly yes`** when an agent holds several keys, or the
  server's `MaxAuthTries` rejects you (`Too many authentication
  failures`) before the right key is offered.
- **Multiplex many connections**: `ControlMaster auto`,
  `ControlPath ~/.ssh/cm/%C` (use `%C`; the literal `%r@%h:%p` overflows
  the socket-path limit), `ControlPersist 10m`. Authenticate once; far
  faster and more reliable than N fresh handshakes.
- **Bastions: `ProxyJump`**, not agent forwarding (the bastion never
  sees your keys). At scale, an SSH **certificate authority** removes
  `known_hosts` TOFU and `authorized_keys` sprawl; lock automation keys
  with `restrict`,`command=`,`from=` in `authorized_keys`.

## Picking the mechanism (one-line guidance)

- Remote command, output and exit code: **`ssh host cmd`** with the
  automation options above. Not a scripted login.
- Local, output and exit code, no TTY: **run it directly** (rung 1).
- Survive disconnect, no TTY: **`systemd-run --user`** or `setsid` +
  sentinel file. More reliable than scraping a detached session.
- Prompt/response, or a program that requires a TTY: **`expect`/
  `pexpect`** (deterministic). PTY needed but no interaction:
  **`script -qec`** or `unbuffer`.
- Detach/reattach a session a human will also use: `systemd-run --pty`,
  GNU `screen`, or `abduco` (interactive UX, not automation).
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
- `references/ssh-in-depth.md`: the ssh_config first-match model, keys and
  agent (`IdentitiesOnly`, agent-forwarding risk), host-key trust
  (`accept-new`) and certificate authorities, `ProxyJump` bastions,
  `ControlMaster` multiplexing, non-interactive semantics (`BatchMode`,
  stdin stealing, double quoting, `--` placement), forwarding, `scp`/
  `rsync`, `authorized_keys` constraints, and an `sshd` hardening baseline.
- `references/terminal-automation-alternatives.md`: the decision matrix and
  deep-dive on direct exec, `setsid`/`nohup`/`disown`, `systemd-run`,
  `script(1)`, `asciinema`, `expect`/`pexpect`, GNU `screen`, `zellij`,
  `abduco`+`dvtm`, `mosh`, and SSH as a transport alternative.
- `references/quick-reference.md`: dense cheat tables (parameter expansion,
  conditionals, redirection, signals, and SSH automation options).

Assets (copy and adapt; they embody every rule above and are ShellCheck
clean):

- `assets/bash-script-skeleton.sh`: production template (strict mode, `ERR`
  and `EXIT` traps, leveled stderr logging, `usage`, `getopts`, `mktemp`
  cleanup, `main "$@"`).
