# SSH in depth

The substantial reference for the SSH parts of `SKILL.md`. SSH is the
terminal an agent most often has to drive non-interactively, and almost
every flaky "remote" automation traces back to one of the items here.
Canonical OpenSSH behaviour (`ssh_config(5)`, `sshd_config(5)`,
`ssh(1)`); stable patterns, not novelties.

## 1. The client config model (get this first)

`~/.ssh/config` (per user) and `/etc/ssh/ssh_config` (system) drive every
`ssh`, `scp`, `sftp`, and `rsync -e ssh`. The one rule that surprises
people:

> For each parameter, the **first obtained value is used**. Earlier
> matches win; nothing later overrides them.

So specific `Host` blocks go **above** the general `Host *`, never below.

```sshconfig
Host deploy
  HostName 10.0.0.7
  User deployer
  IdentityFile ~/.ssh/deploy_ed25519
  IdentitiesOnly yes

Host *.internal
  ProxyJump bastion
  User svc

Host *                       # defaults; must be LAST
  ServerAliveInterval 30
  ServerAliveCountMax 3
  ControlMaster auto
  ControlPath ~/.ssh/cm/%C
  ControlPersist 10m
  HashKnownHosts yes
  StrictHostKeyChecking accept-new
```

- `Match` is the conditional form: `Match host h`, `Match user u`,
  `Match exec "cmd"` (runs a local command; non-zero means no match),
  `Match final` (evaluated on a second pass, after everything else),
  `Match canonical` (after hostname canonicalization). Use it when a
  `Host` glob is not expressive enough.
- `Include ~/.ssh/config.d/*` splits config into managed fragments
  (processed where the `Include` appears, so placement still obeys the
  first-match rule).
- `CanonicalizeHostname yes` + `CanonicalDomains example.com` lets short
  names resolve to FQDNs consistently before matching.

## 2. Keys and the agent

- Generate `ed25519` (small, fast, no parameter-choice footguns):
  `ssh-keygen -t ed25519 -a 100 -C "user@host 2026-05"`. Use a
  passphrase. Only use RSA if a peer cannot do ed25519, and then
  `>= 3072` bits (`-t rsa -b 4096`). Avoid `ecdsa` (NIST-curve trust
  concerns). For hardware-backed keys use `ed25519-sk` / `ecdsa-sk`
  (FIDO2). OpenSSH's modern private-key format is the default since 7.8.
- Agent: `ssh-add -t 8h key` adds with a lifetime; `AddKeysToAgent yes`
  loads on first use. Set a default identity lifetime so a forgotten
  unlock does not persist forever.
- `IdentitiesOnly yes` is not optional once an agent holds several keys.
  Without it, the client offers every agent key in turn; servers with a
  low `MaxAuthTries` reject you with `Too many authentication failures`
  before reaching the right key. Pin `IdentityFile` + `IdentitiesOnly`
  per host.
- Agent forwarding (`ForwardAgent` / `-A`) exposes your agent socket to
  root on the intermediate host (they can impersonate you to anything
  the agent can reach). Default it off. Prefer `ProxyJump` (the bastion
  never sees your keys). If forwarding is unavoidable, use
  destination-constrained keys (`ssh-add -h dest`, OpenSSH 8.9+) and/or
  confirmation (`ssh-add -c`).

## 3. Host keys: trust on first use, and the way out

`known_hosts` is trust-on-first-use. The relevant control is
`StrictHostKeyChecking`:

- `yes`: refuse hosts not already in `known_hosts`. Safest, needs
  prepopulation.
- `accept-new`: accept a **new** host automatically, still refuse a
  **changed** key. This is the correct automation setting.
- `ask` (interactive default): prompts; in a script with no TTY it
  hangs or fails. Never rely on it in automation.
- `no` / `off`: accept new, ignore changes, and write them. Dangerous;
  defeats MITM protection. Never in automation.

Supporting practice:

- `UpdateHostKeys yes` (default when safe) lets servers rotate host keys
  seamlessly without a scary warning.
- Prepopulate for strict checking: `ssh-keyscan -t ed25519 host >>
  ~/.ssh/known_hosts` (verify out of band). Manage entries with
  `ssh-keygen -F host` (find) and `ssh-keygen -R host` (remove).
- `HashKnownHosts yes` hides hostnames at rest (note: you then cannot
  grep the file; use `ssh-keygen -F`).
- `VerifyHostKeyDNS yes` validates against SSHFP DNS records (needs
  DNSSEC to be meaningful).

**The scalable answer is an SSH certificate authority**, which removes
TOFU and `authorized_keys` sprawl entirely:

```bash
# Sign a host key; clients trust the CA via one @cert-authority line.
ssh-keygen -s host_ca -I web01 -h -n web01.example.com -V +52w host_ed25519.pub
# Sign a user key, short lifetime, explicit principals.
ssh-keygen -s user_ca -I alice -n deployer,admin -V +8h alice_ed25519.pub
```

Client `known_hosts`: `@cert-authority *.example.com ssh-ed25519 AAAA...`.
Server: `TrustedUserCAKeys /etc/ssh/user_ca.pub`. Short-lived user certs
mean no per-host key distribution and natural expiry.

## 4. Jump hosts and bastions

`ProxyJump` (OpenSSH 7.3+) is the modern, correct mechanism:

```bash
ssh -J user@bastion:22 user@10.0.0.7        # chain: -J host1,host2
```

```sshconfig
Host 10.0.* internal-*
  ProxyJump bastion
```

It beats the legacy `ProxyCommand ssh -W %h:%p bastion` (still valid,
and the right tool when you need a custom connector such as
`cloudflared`/`aws ssm`), and it beats agent forwarding because the
bastion never handles your credentials. Tokens for `ProxyCommand`:
`%h` host, `%p` port, `%r` remote user, `%n` original name, `%C` a hash
of the connection (handy for socket paths).

## 5. Connection multiplexing (the biggest automation win)

Reuse one authenticated TCP connection for many sessions. It removes
per-command handshake latency and authenticates once, which is both
faster and more reliable for automation that opens many connections:

```sshconfig
Host *
  ControlMaster auto
  ControlPath ~/.ssh/cm/%C
  ControlPersist 10m
```

- Use `%C` (a hash) for `ControlPath`. The literal `%r@%h:%p` form
  overflows the ~104-byte UNIX socket path limit on long hostnames and
  fails opaquely. Create the directory (`mkdir -p ~/.ssh/cm`).
- Manage the master explicitly: `ssh -O check host`,
  `ssh -O exit host`, `ssh -O stop host`.
- Caveats: the first connection owns the master; if it dies, its
  children die. A stale socket after a crash makes the next connect
  hang briefly then fall back; a cron/systemd `ssh -O exit` or
  `ControlPersist` timeout cleans up. Server `MaxSessions` caps
  multiplexed channels.

## 6. Non-interactive automation semantics

This is where "it worked by hand but hangs in CI" comes from.

- **Exit status passes through.** `ssh host cmd` returns the remote
  command's exit code (255 is reserved for ssh's own connection
  failure). No PTY by default.
- **`BatchMode=yes`**: never prompt for a password, passphrase, or host
  key; fail immediately instead. Mandatory in automation so a missing
  key fails fast rather than blocking on a hidden prompt forever. Pair
  with key or certificate auth.
- **Bound the connection.** `ConnectTimeout=10`, `ConnectionAttempts=2`,
  and `ServerAliveInterval=15` + `ServerAliveCountMax=4` so a
  black-holed peer is detected in seconds instead of hanging the job
  indefinitely. `ServerAlive*` is encrypted application-level keepalive;
  prefer it over `TCPKeepAlive` (spoofable, coarser).
- **The stdin-stealing bug.** `ssh` reads its stdin and forwards it. In
  `while read line; do ssh host ...; done < hosts.txt`, the first `ssh`
  drains the rest of `hosts.txt` and the loop runs once. Fix: `ssh -n`
  (stdin from `/dev/null`) or redirect `< /dev/null`, unless you are
  deliberately piping data to the remote command.
- **Remote command quoting is double expansion.** Everything after the
  destination is joined and handed to the remote login shell, so it is
  expanded **locally then remotely**. Single-quote to defer, or push a
  script instead of quoting:

  ```bash
  ssh host 'echo "$HOSTNAME"'                  # expands on the remote
  ssh host bash -s -- arg1 arg2 < local_script.sh
  printf '%q ' "${cmd[@]}" | ssh host bash -s  # safe argv transport
  ```

- **`--` is not a local option terminator after the host.** `ssh`
  stops parsing its own options at the destination; `ssh host -- cmd`
  asks the remote shell to run `-- cmd`. Put a guard before the host
  (`ssh [opts] -- host cmd`) only if the hostname could start with `-`.
- **Quiet the channel for parsing.** `LogLevel=ERROR` (or `-q`)
  suppresses banner/MOTD noise mixing into captured stdout; better,
  run a specific command rather than a login shell.
- **`-tt` forces a PTY** when the remote side demands one (`sudo` with
  `requiretty`, interactive tools). It also makes the remote see a
  terminal, which changes buffering and signal behaviour, so use it
  only when needed and remember it can echo input back.

A robust automation invocation, distilled:

```bash
ssh -n -T \
  -o BatchMode=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=accept-new \
  "$host" 'set -Eeuo pipefail; deploy --version'
rc=$?   # the remote command's real exit code
```

## 7. Forwarding and tunnels

- `-L [bind:]lport:dsthost:dstport`: local to remote.
- `-R rport:dsthost:dstport`: remote to local (server needs
  `GatewayPorts` for non-loopback binds).
- `-D port`: dynamic SOCKS proxy.
- `-N` (no remote command, tunnel only), `-f` (background once
  forwards/auth are up).
- `ExitOnForwardFailure yes`: if a requested forward cannot be
  established, the connection fails instead of silently continuing
  without the tunnel. Essential when a script depends on the tunnel.
- On a multiplexed master, add/drop forwards live with
  `ssh -O forward` / `ssh -O cancel`.

## 8. File transfer

- `scp` uses the SFTP protocol by default since OpenSSH 9.0; `scp -O`
  forces the legacy SCP protocol for servers without SFTP or for old
  `~`/wildcard expansion. `scp` is effectively legacy: prefer `sftp` or
  `rsync`.
- `rsync -e 'ssh -o BatchMode=yes' -az src/ host:/dst/` over a
  multiplexed master is the fast, resumable, idempotent default for
  bulk or repeated transfers.
- `scp -3 src host1:f host2:f` routes host-to-host via the local
  machine; pass ssh options with `scp -o`.

## 9. Locking down the server side for automation accounts

A deploy or CI key should be able to do exactly one thing. Constrain it
in `authorized_keys`:

```
restrict,from="10.0.0.0/24",command="/usr/local/bin/deploy-hook" ssh-ed25519 AAAA... ci@deploy
```

- `restrict` (7.2+) is the safe umbrella: disables PTY, all forwarding,
  X11, agent, and user-rc. Re-enable only what is needed
  (`restrict,pty`).
- `command="..."` is a forced command: only it runs; the client's
  requested command is in `$SSH_ORIGINAL_COMMAND` (validate it, do not
  `eval` it).
- `from="cidr/host"` restricts source; `expiry-time="..."` adds a hard
  expiry. Prefer short-lived **certificates** over long-lived
  `authorized_keys` entries when you have a CA.

## 10. sshd hardening (defensive baseline)

Server hardening is its own discipline; the high-value, stable settings
(see `sshd_config(5)` and current guidance such as sshaudit.com rather
than hardcoding cipher lists that age):

- `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
  `PubkeyAuthentication yes`.
- `PermitRootLogin prohibit-password` (or `no`).
- `AuthenticationMethods publickey` (or `publickey,keyboard-interactive`
  to require a second factor).
- `AllowGroups ssh-users` (allowlist) and per-group `Match` blocks.
- `MaxAuthTries 3`, `LoginGraceTime 20`, `MaxStartups 10:30:60`.
- Disable what you do not use: `AllowTcpForwarding no`,
  `X11Forwarding no`, `AllowAgentForwarding no`.
- `ClientAliveInterval 300` / `ClientAliveCountMax 2` to reap dead
  sessions.
- **Validate before reload so you do not lock yourself out:**
  `sshd -t` (syntax) and `sshd -T` (dump effective config); keep a
  second authenticated session open during changes. Audit with
  `ssh-audit`.

## 11. Automation pitfalls checklist

- No `BatchMode=yes`: a missing key turns into a silent password prompt
  that hangs CI forever.
- No `ConnectTimeout`/`ServerAlive*`: a dead or firewalled host hangs
  the job with no error.
- `StrictHostKeyChecking` left at the interactive default: first
  contact prompts and blocks; or set to `no` and lose MITM protection.
  Use `accept-new` (or prepopulate and use `yes`).
- `ssh` inside a `while read` loop without `-n`: the loop runs once
  because ssh ate the input.
- `--` placed after the host expecting local option parsing: it becomes
  the remote command.
- Agent forwarding to a host you do not fully trust: socket hijack. Use
  `ProxyJump`.
- `ControlPath` as `%r@%h:%p`: silently fails on long hostnames
  (socket path limit). Use `%C`.
- No multiplexing for many short connections: slow and more failure
  surface; enable `ControlMaster` + `ControlPersist`.
- Relying on `scp` semantics that changed in OpenSSH 9 (SFTP backend):
  use `-O` only as a deliberate fallback; prefer `rsync`/`sftp`.
- Non-idempotent remote command retried after a network blip: corrupts
  state. Make the remote action idempotent and capture its real `rc`.
