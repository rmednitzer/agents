# Bash safety and the canonical pitfalls

Reference for the safety baseline in `SKILL.md`. Sources are the
long-standing community canon: Greg Wooledge's BashPitfalls and BashFAQ
(`mywiki.wooledge.org`), the Google Shell Style Guide, and the ShellCheck
wiki. The patterns here are stable and validated, not novel.

## 1. Strict mode, dissected honestly

`set -Eeuo pipefail` plus `IFS=$'\n\t'` is the right default, but every
flag has sharp edges. Knowing the edges is the difference between a safety
net and a false sense of safety.

### `-e` / errexit: a net with holes

It exits on an "unhandled" non-zero. The exceptions are numerous and are
the usual reason a "strict" script still silently continues after a
failure:

- A command on the left of `&&` or `||`, or anywhere in an `if`, `while`,
  or `until` condition, or negated with `!`, does not trigger exit.
- A function invoked in any of those contexts has errexit **suppressed for
  its entire body** (a frequent and surprising source of missed errors).
- `cmd || true` and `cmd &` are not "failures".
- `local x=$(cmd)` masks the failure of `cmd`: `local` itself succeeds, so
  the non-zero is lost. Split it:

  ```bash
  local x
  x=$(cmd)        # now a failing cmd is visible to errexit
  ```

- Arithmetic that evaluates to zero is a "non-zero exit": `(( count++ ))`
  when `count` was `0` returns status 1 and, under `-e`, **exits the
  script**. Use `(( ++count ))`, or `(( count++ )) || true`, or
  `count=$((count+1))`.

Conclusion: treat `-e` as defense in depth. Real error handling is an
explicit `ERR` trap plus checking the commands that matter.

### `-E` / errtrace: mandatory with a trap

Without `-E`, an `ERR` (and `RETURN`/`DEBUG`) trap is **not** inherited by
shell functions, command substitutions, or subshells. A script with
`set -e` and `trap ... ERR` but no `-E` will miss failures that happen
inside any function, which is most of them. Always pair them.

### `-u` / nounset: deliberate defaults

Referencing an unset variable aborts. This is good. Make defaults explicit:

```bash
: "${LOG_LEVEL:=info}"     # set if unset
dest="${1:?usage: tool DEST}"   # abort with a message if missing
files=( "${FILES[@]:-}" )  # safe empty array under older bash
```

Note `$@`/`$*` are exempt, so `"$@"` is always safe even when empty.

### `-o pipefail`: required, with a SIGPIPE caveat

Without it, `false | true` exits 0 and the failure of `false` vanishes.
With it, the pipeline takes the first non-zero. The caveat: a consumer
that legitimately closes the pipe early (`... | head -1`) makes the
producer die with `SIGPIPE` (exit 141), which `pipefail` now reports as
failure. Handle the known-good case explicitly, for example:

```bash
set +o pipefail
first=$(long_producer | head -1)
set -o pipefail
```

or restructure so the early-exit consumer is not in a checked pipeline.
`PIPESTATUS` (`${PIPESTATUS[@]}`) gives every stage's status when you need
to inspect rather than abort.

### `IFS=$'\n\t'`

Removes space from the word-splitting set, so a missed quote splits only on
tab and newline instead of every space. It reduces blast radius; it is not
a substitute for quoting, and it changes the behavior of unquoted `$*` and
some tools. Quote anyway.

## 2. The canonical pitfalls (wrong then right)

High-frequency entries condensed from BashPitfalls. If you review shell and
internalize one section, make it this one.

**Unquoted expansion (word splitting and globbing).**

```bash
cp $file $dest          # wrong: splits, globs
cp -- "$file" "$dest"   # right: quoted; -- ends option parsing
```

**Iterating command output / parsing `ls`.**

```bash
for f in $(ls *.mp3); do ...        # wrong: splits on whitespace, reparses
for f in ./*.mp3; do ...            # right: glob; ./ guards leading dash
# files may contain newlines: use NUL
while IFS= read -r -d '' f; do ...; done < <(find . -name '*.mp3' -print0)
```

**Test syntax and numeric vs string compare.**

```bash
[ $x = "y" ]              # wrong: unquoted; breaks if $x empty or has spaces
[[ $x == y ]]             # right: no split inside [[ ]]; == allows globs
[[ $a > $b ]]             # wrong: STRING comparison (lexical)
(( a > b ))               # right: numeric
[ "$x" -eq 3 ]            # ok numeric in [ ], but quote the var
```

**`if` takes a command, not a `[`-shaped expression.**

```bash
if [ grep -q foo file ]; then ...   # wrong
if grep -q foo file; then ...       # right
```

**`cd` without a guard.**

```bash
cd "$dir"; rm -rf ./*               # wrong: cd may fail, rm runs in $PWD
cd "$dir" || exit 1; rm -rf ./*     # right
```

**Pipeline subshell loses variables.**

```bash
n=0; printf 'a\nb\n' | while read -r x; do ((n++)); done; echo "$n"  # 0
n=0; while read -r x; do ((n++)); done < <(printf 'a\nb\n'); echo "$n"  # 2
```

**`read` strips and splits unless told not to.**

```bash
read line                  # wrong: mangles backslashes, trims, splits
IFS= read -r line          # right: raw, whole line
```

**Redirection order (left to right).**

```bash
cmd 2>&1 >log    # wrong: stderr -> old stdout (tty), stdout -> log
cmd >log 2>&1    # right: both -> log
```

**Brace expansion happens before parameter expansion.**

```bash
for i in {1..$n}; do ...            # wrong: literal "{1..$n}"
for ((i=1; i<=n; i++)); do ...      # right
```

**Heredoc and tilde and single quotes.**

```bash
echo <<EOF ... EOF        # wrong: echo ignores stdin; use cat <<EOF
foo="~/bar"               # wrong: tilde not expanded in quotes
foo="$HOME/bar"           # right
sed 's/$x/y/'             # wrong: single quotes block $x
sed "s/$x/y/"             # right (and beware metacharacters in $x)
```

**Arrays: the whole point is safe lists.**

```bash
args="-a -b"; cmd $args             # wrong: one string, then split
args=(-a -b); cmd "${args[@]}"      # right: exact argv, even with spaces
"${arr[@]}"                          # all elements, each its own word
"${#arr[@]}"  "${!arr[@]}"           # count; indices
```

**`function` keyword with `()`.** Use `name() { ...; }` (portable form),
not `function name() { ... }`.

## 3. Quoting decision rule

Quote unless you have a specific, commented reason not to. The only common
deliberate-unquote cases: intentional word splitting of a known-safe value,
intentional globbing, and a few numeric `[[ ]]`/`(( ))` contexts where no
splitting occurs. `"$@"` is the magic form that preserves each argument
exactly; `$*` and `"$*"` join, so they are almost never what you want for
argument forwarding.

## 4. ShellCheck is not optional

Run `shellcheck script.sh` (or `shellcheck -x` to follow `source`d files)
in CI and before review. It mechanically catches most of section 2. Rules:

- Treat findings as errors. A clean ShellCheck run is the floor, not the
  goal.
- Disable a check only with a justification on the directive line, scoped as
  narrowly as possible (a single line, not a whole file):

  ```bash
  # shellcheck disable=SC2016  # single quotes deliberate: awk program, not shell
  awk '{print $1}' file
  ```

- `# shellcheck shell=bash` at the top of a sourced fragment with no
  shebang tells it the dialect.
- Common true positives worth knowing: SC2086 (unquoted expansion), SC2046
  (unquoted `$(...)`), SC2155 (`local x=$(cmd)` masks status), SC2164
  (`cd` unchecked), SC2068 (`$@` unquoted), SC2207 (use `mapfile`/`read -a`,
  not `arr=( $(cmd) )`).

## 5. Bash vs not Bash

Per the Google guide: shell is for short utilities and wrappers. If the
script grows past roughly 100 lines, grows non-trivial data structures or
control flow, needs to manipulate anything other than files and process
exit codes, or must be maintained by people who do not read shell fluently,
rewrite it in a real language. Recognizing this early is itself a best
practice. When you do stay in shell, target `bash` explicitly with
`#!/usr/bin/env bash`; do not write `#!/bin/sh` and then use `[[ ]]`,
arrays, or `pipefail` (those are not POSIX `sh`, and `sh` may be `dash`).
