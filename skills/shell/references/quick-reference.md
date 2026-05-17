# Shell and SSH quick reference

Dense lookup tables. Everything here is stated and explained in the other
references; this is the at-a-glance form.

## Strict-mode header

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
trap 'rc=$?; echo "ERR rc=$rc line=$LINENO cmd=$BASH_COMMAND" >&2; exit $rc' ERR
trap cleanup EXIT
```

## Parameter expansion

| Form | Meaning |
|---|---|
| `${v:-d}` | use `d` if `v` unset or empty |
| `${v:=d}` | assign `d` to `v` if unset or empty |
| `${v:?msg}` | error with `msg` if `v` unset or empty |
| `${v:+x}` | `x` if `v` is set and non-empty, else empty |
| `${#v}` | length of `v` |
| `${v#pat}` / `${v##pat}` | strip shortest / longest prefix matching `pat` |
| `${v%pat}` / `${v%%pat}` | strip shortest / longest suffix |
| `${v/old/new}` / `${v//old/new}` | replace first / all |
| `${v:offset:len}` | substring |
| `${v^^}` / `${v,,}` | upper / lower case |
| `${v@Q}` | value quoted so it can be reused as input |
| `${arr[@]}` `${#arr[@]}` `${!arr[@]}` | elements, count, indices |
| `${!prefix@}` | names of variables starting with `prefix` |

## Test and arithmetic

| Need | Use |
|---|---|
| String equal / glob match | `[[ $a == "$b" ]]` / `[[ $a == pre* ]]` |
| Regex match | `[[ $a =~ ^[0-9]+$ ]]` (groups in `${BASH_REMATCH[@]}`) |
| Numeric compare | `(( a > b ))`, `(( a == b ))` |
| Empty / non-empty string | `[[ -z $a ]]` / `[[ -n $a ]]` |
| File exists / dir / symlink | `[[ -e p ]]` / `[[ -d p ]]` / `[[ -L p ]]` |
| File readable / writable / executable | `[[ -r p ]]` / `[[ -w p ]]` / `[[ -x p ]]` |
| Non-empty file / newer than | `[[ -s p ]]` / `[[ a -nt b ]]` |
| Variable is set (even if empty) | `[[ -v name ]]` |
| And / or inside one test | `[[ cond1 && cond2 ]]`, `[[ cond1 || cond2 ]]` |

Inside `[[ ]]`, `<` and `>` compare strings. For numbers always use
`(( ))`. Never use `expr`, `let`, or `$[ ]`.

## Redirection

| Want | Write |
|---|---|
| stdout and stderr to a file | `cmd >f 2>&1` (order matters) |
| append both | `cmd >>f 2>&1` |
| stderr only to a file | `cmd 2>f` |
| discard stdout, keep stderr | `cmd >/dev/null` |
| stdin from a string | `cmd <<<"$s"` |
| heredoc, no expansion | `cmd <<'EOF' ... EOF` |
| heredoc, with expansion | `cmd <<EOF ... EOF` |
| read process output without subshell | `while ...; do ...; done < <(cmd)` |
| capture every pipeline stage status | `${PIPESTATUS[@]}` after the pipe |
| open fd for locking | `exec 9>lockfile; flock -n 9` |

`cmd 2>&1 >f` is the classic bug: it points stderr at the **old** stdout
(the terminal), then sends stdout to `f`. Put `>f` first.

## Exit codes worth standardizing

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | generic failure |
| 2 | usage / bad arguments (matches many CLIs) |
| 75 | `EX_TEMPFAIL`: transient, retry exhausted (sysexits) |
| 124 | `timeout` killed the command |
| 125 | `timeout` itself failed |
| 126 | found but not executable |
| 127 | command not found |
| 128+N | terminated by signal N (130 = `SIGINT`, 143 = `SIGTERM`) |

## Common signals

| N | Name | Use |
|---|---|---|
| 1 | HUP | terminal closed; many daemons reload config |
| 2 | INT | Ctrl-C; request graceful stop |
| 9 | KILL | cannot be trapped; last resort |
| 13 | PIPE | reader closed early (exit 141 with `pipefail`) |
| 15 | TERM | polite stop; the default `kill` |
| 18 | CONT / 19 STOP | resume / pause |

Trap `INT`/`TERM`, convert to a clean exit so the `EXIT` trap still runs.
`KILL` and `STOP` cannot be trapped.

## SSH automation

```bash
ssh -n -T -o BatchMode=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=accept-new host 'set -Eeuo pipefail; cmd'
rc=$?   # remote command's real exit code (255 = ssh connection failure)
```

| Need | Option / keyword |
|---|---|
| Never prompt; fail fast | `-o BatchMode=yes` |
| Do not hang on a dead host | `-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4` |
| New host ok, changed key refused | `-o StrictHostKeyChecking=accept-new` |
| Stop ssh eating loop stdin | `-n` (or `< /dev/null`) |
| No PTY / force PTY | `-T` / `-tt` (only if remote needs it) |
| Only this key, avoid `Too many auth failures` | `-o IdentitiesOnly=yes -i key` |
| Jump host (not agent forwarding) | `-J user@bastion[,user@bastion2]` |
| Reuse one auth'd connection | `ControlMaster auto` / `ControlPath ~/.ssh/cm/%C` / `ControlPersist 10m` |
| Run a local script remotely | `ssh host bash -s -- args < script.sh` |
| Fail if a tunnel cannot open | `-o ExitOnForwardFailure=yes` |
| Manage a master | `ssh -O check\|exit\|stop host` |
| Bulk/idempotent copy | `rsync -e 'ssh -o BatchMode=yes' -az src/ host:/dst/` |

Traps: `ssh host -- cmd` runs `-- cmd` remotely (the `--` is not a local
terminator; it goes before the host). Remote args are expanded locally
then by the remote shell (single-quote or push a script). `known_hosts`
hashed (`HashKnownHosts yes`) means use `ssh-keygen -F`/`-R`, not grep.

## Alternatives picker (one line)

| Goal | Tool |
|---|---|
| Output + exit code, foreground | direct exec, capture `$?` |
| Durable, no TTY | `systemd-run --user` (or `setsid` + `.rc` file) |
| Prompt/response, or program needs a TTY | `expect` / `pexpect` |
| PTY needed but no interaction | `script -qec 'cmd' /dev/null` / `unbuffer` |
| Detach/reattach for a human | `systemd-run --pty` / `screen` / `abduco` |
| Record for a human | `script -c` / `asciinema` |
| Remote command, no PTY | `ssh -T -o BatchMode=yes host cmd` (no `--` after host) |
