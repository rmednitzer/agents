# Robust Bash script architecture

How to structure anything larger than a one-liner. Pairs with
`assets/bash-script-skeleton.sh`, which is this document made executable.

## 1. File layout (top to bottom)

1. Shebang: `#!/usr/bin/env bash` (finds `bash` via `PATH`; avoids the
   `/bin/bash` vs `/usr/local/bin/bash` split on BSD and macOS).
2. A one-line purpose comment, then `SPDX-License-Identifier` if the repo
   uses it.
3. `set -Eeuo pipefail` and `IFS=$'\n\t'`.
4. Readonly constants in `UPPER_SNAKE`, declared once near the top.
5. Functions, lowercase, each with a header comment for non-obvious ones
   (globals read or written, arguments, stdout, return).
6. A `main` function.
7. The single bottom line: `main "$@"`.

`main "$@"` last means the whole file parses before anything runs, so a
truncated download or partial edit cannot execute half a script.

## 2. Logging and output discipline

The script's **result** goes to stdout. **Everything else** (progress,
warnings, errors) goes to stderr. This is what lets the script be used in a
pipeline without corrupting the data stream.

```bash
readonly LOG_LEVEL="${LOG_LEVEL:-info}"

_log() {  # _log LEVEL MESSAGE...
  local level=$1; shift
  local -A order=([debug]=0 [info]=1 [warn]=2 [error]=3)
  (( order[$level] < order[$LOG_LEVEL] )) && return 0
  printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*" >&2
}
log_debug() { _log debug "$@"; }
log_info()  { _log info  "$@"; }
log_warn()  { _log warn  "$@"; }
log_error() { _log error "$@"; }
die() { log_error "$@"; exit 1; }
```

Use UTC ISO 8601 timestamps. Do not invent a colored framework; this is
enough and has no dependencies.

## 3. Traps: cleanup and error reporting

Cleanup belongs in an `EXIT` trap so it runs on success, on `set -e`
abort, and on a signal. It must be idempotent (it can run after partial
setup) and must never expand to a destructive command when a variable is
empty.

```bash
cleanup() {
  local rc=$?
  [[ -n "${WORKDIR:-}" && -d "$WORKDIR" ]] && rm -rf -- "$WORKDIR"
  # release other resources here; each guarded, each idempotent
  return "$rc"
}
trap cleanup EXIT

on_err() {  # requires set -E
  local rc=$? line=$1
  log_error "failed (rc=$rc) at line $line: ${BASH_COMMAND}"
  exit "$rc"
}
trap 'on_err $LINENO' ERR

# Translate signals into a normal exit so the EXIT trap still fires.
trap 'log_warn "interrupted"; exit 130' INT
trap 'log_warn "terminated"; exit 143' TERM
```

`BASH_COMMAND` in the `ERR` trap is the command that failed; `$LINENO`
passed as an argument captures the line at trap time. The signal traps
exit with the conventional `128 + signal` codes so callers can tell how
the script ended.

## 4. Scratch space and atomic writes

Never hand-build temp paths. `mktemp` avoids collisions and races:

```bash
WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/tool.XXXXXXXX")
```

Writing a file "in place" with `>` is not atomic: a crash leaves a
truncated file, and a reader can observe the half-written state. Write to a
temp file on the **same filesystem** as the target, then `mv` (rename is
atomic within a filesystem):

```bash
write_atomic() {  # write_atomic DEST < content
  local dest=$1 tmp
  tmp=$(mktemp -- "${dest}.XXXXXX")
  cat > "$tmp" && mv -f -- "$tmp" "$dest"
}
```

## 5. Argument parsing

`getopts` handles short options and is built in. It does not do long
options; the portable pattern is a `while`/`case` over `$@` with `shift`,
and a `--` sentinel to stop parsing.

```bash
usage() { cat >&2 <<'EOF'
Usage: tool [-v] [-n] [-o OUT] -- ARG...
  -v   verbose (repeatable)
  -n   dry run
  -o   output path
EOF
}

DRY_RUN=0 VERBOSE=0 OUT=""
while (( $# )); do
  case $1 in
    -h|--help) usage; exit 0 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -v|--verbose) (( ++VERBOSE )) ;;
    -o|--out) OUT=${2:?-o needs a value}; shift ;;
    --) shift; break ;;
    -*) usage; die "unknown option: $1" ;;
    *) break ;;
  esac
  shift
done
```

Always support `-h`/`--help`, and always support `--` so an argument that
starts with `-` (or a filename like `-rf`) cannot be misread as an option.

## 6. Idempotency and dry-run

A state-changing script should be safe to run twice. Check before you
mutate (`mkdir -p`, `[[ -e ]]` guards, "create if absent"), and make the
end state, not the steps, the contract.

Thread a dry-run mode through a single wrapper so it is impossible to
forget:

```bash
run() {  # log the command; execute unless DRY_RUN
  log_info "+ $*"
  (( DRY_RUN )) && return 0
  "$@"
}
run rm -rf -- "$stale"
```

`run` takes argv, not a string, so there is no re-quoting and no `eval`.

## 7. Retries: only for idempotent, transient failures

Retrying a non-idempotent operation corrupts state. Retrying a
deterministic failure just wastes time. Retry only idempotent operations
that fail for transient reasons (network, lock contention), with
exponential backoff, jitter (to avoid synchronized retry storms), a cap,
and a distinct give-up exit code.

```bash
retry() {  # retry MAX_ATTEMPTS BASE_DELAY -- CMD...
  local max=$1 base=$2; shift 2; [[ $1 == -- ]] && shift
  local attempt=1 delay
  until "$@"; do
    (( attempt >= max )) && { log_error "giving up after $attempt: $*"; return 75; }
    delay=$(( base * 2 ** (attempt - 1) ))
    delay=$(( delay + RANDOM % (delay + 1) ))   # full jitter
    log_warn "attempt $attempt failed; retrying in ${delay}s"
    sleep "$delay"
    (( ++attempt ))
  done
}
```

Exit `75` is `EX_TEMPFAIL` from `sysexits.h`, a useful convention for "I
gave up on a transient failure" so a supervisor can distinguish it from a
hard error.

## 8. Timeouts: bound everything that can hang

Any network call, any wait on another process, any `wait-for`, anything
that can block forever, gets a `timeout`:

```bash
if ! timeout 30s curl -fsS "$url" -o "$out"; then
  rc=$?
  (( rc == 124 )) && die "timed out fetching $url"
  die "fetch failed (rc=$rc): $url"
fi
```

`timeout` exits `124` on timeout. Use `timeout -k 5s 30s ...` to send
`SIGKILL` 5 seconds after the initial `SIGTERM` if the child ignores it.

## 9. Locking: one instance, or a critical section

Use `flock` on a descriptor. It is released automatically when the fd
closes (including on crash), which a lock file or lock directory checked
with `[[ -e ]]` is not (those leak on `kill -9`).

```bash
exec 9>"${TMPDIR:-/tmp}/tool.lock"
flock -n 9 || die "another instance is running"
# critical section; lock auto-released when fd 9 closes at exit
```

## 10. Bounded concurrency

Unbounded `cmd & cmd & ...` forks until the box falls over. Bound it. In
order of preference:

- `xargs -P "$(nproc)" -n1 -0` with NUL-delimited input: simple, ubiquitous,
  back-pressured.
- GNU `parallel` when you need per-job logs, retries, or a join.
- A hand-rolled pool with `wait -n` (Bash 5.1+) when the work is generated
  dynamically:

  ```bash
  max=$(nproc); running=0
  for item in "${items[@]}"; do
    work "$item" &
    if (( ++running >= max )); then wait -n; (( running-- )); fi
  done
  wait
  ```

Collect child status with `wait "$pid"` per child, or check `$?` after
`wait -n`; a backgrounded failure is invisible to `set -e` otherwise.

## 11. Preflight: fail fast on a missing dependency

Check the environment before doing work, so the script fails in the first
second with a clear message instead of halfway through:

```bash
require() {  # require CMD...
  local missing=0 c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || { log_error "missing: $c"; missing=1; }
  done
  (( missing )) && die "install the missing dependencies and retry"
}
require curl jq tmux
```

Also assert the Bash version if you use version-specific features
(`mapfile`, `wait -n`, associative arrays, `${var@Q}`):

```bash
(( BASH_VERSINFO[0] >= 5 )) || die "bash >= 5 required (have $BASH_VERSION)"
```

## 12. Review checklist

- Shebang `env bash`; `set -Eeuo pipefail`; `IFS` set.
- Every expansion quoted, or unquoted with a comment saying why.
- No `ls` parsing, no `for x in $(...)`; globs or NUL streams.
- `EXIT` trap cleanup, idempotent, empty-var guarded; `ERR` trap reports.
- Signals trapped to conventional exit codes.
- File writes atomic; state changes idempotent; `--` honored.
- Retries only on idempotent transient ops, with jitter and a cap.
- Hang-prone calls wrapped in `timeout`; `124` handled.
- Single-instance enforced with `flock` if it matters.
- Concurrency bounded; child exit status collected.
- Dependencies and Bash version checked up front.
- ShellCheck clean; disables justified inline.
