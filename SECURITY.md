# Security Policy

## Reporting a vulnerability

Open a private security advisory on GitHub: https://github.com/rmednitzer/agents/security/advisories/new

Do not file public issues for security-relevant findings.

Targets:
- Acknowledgement: 7 days.
- Initial assessment: 14 days.
- Coordinated disclosure preferred.

## Scope

In scope:
- Harness contract violations (sandbox escape, action budget bypass, tool-use authorization bypass, including governance/budget bypass via MCP-exposed tools).
- Memory isolation failures (cross-namespace read or write, lineage tampering), per-key ACL bypass, and encryption-at-rest weaknesses in `EncryptedStore`.
- Skill loading vulnerabilities (path traversal via skill name or archive member, symlink dereference via a crafted local mirror or a non-file archive member, code execution via crafted SKILL.md or bundled assets) and out-of-tree workload loading (filesystem path or installed-package entry point).

Out of scope:
- Issues in upstream dependencies (report upstream first; reference here once a fix lands). Dependencies are lockfile-pinned (`uv.lock`); Dependabot proposes `pip` and `github-actions` updates.
- Findings requiring physical access to a host running the harness.

## Hardening posture

- Skill install (`GitHubSkillSource`, `MarketplaceSkillSource`): one hardened path bounds the archive download, member count, per-member size, and total uncompressed size; a non-file member inside the wanted subtree is rejected (not silently skipped); each member read is clamped to the remaining budget. An optional `sha256` and a `SignatureVerifier` hook (`signature` / `verify_signature`) verify the tarball. A branch `ref` is mutable; pin an immutable ref (commit SHA or release tag) plus a checksum (or a signature) for tamper-evident installs. `LocalSkillSource` copies regular files only and refuses a symlink anywhere in the subtree (a crafted mirror cannot exfiltrate a host secret into the bundle).
- Governance enforcement: a SOFT governance predicate now produces a SOFT reject the runtime acts on (logs-and-continues, or with `soft_reject_as_error` surfaces a typed rejection), not a silent APPROVE; a HARD predicate still hard-rejects. Composition keeps the strictest severity on a predicate-name collision, so a reviewed obligation cannot be silently weakened.
- Approval-resume binding: on resume, a resolved approval is matched against the proposed call by the full `(tool, arguments)` tuple, not by `tool` alone. A stale approval for one set of arguments cannot satisfy a fresh call to the same tool with different arguments (`BL-193`). The default `HarnessToolGuard` mints a new `interruption_id` per check, so the id is not a stable cross-pause handle; the tuple is the load-bearing key.
- Audit path resilience: the redactor walks event payloads with a configurable depth cap (`Redactor.max_depth = 64`, `BL-200`) so a cyclic or pathologically-deep `state_snapshot` cannot crash the emit chain via `RecursionError`. Same invariant the `BL-167` ordering fix established for the contract-event side: the audit path must terminate cleanly on every input, including adversarial ones, so a malicious or buggy payload cannot suppress its own audit by killing the sink.
- Supply chain: a blocking dependency-audit gate (`pip-audit` over the exported lockfile) and a REUSE-compliance gate (`reuse lint`) run in CI; the `release` workflow emits a CycloneDX SBOM and attests build provenance. GitHub Actions are tag-pinned (commit-SHA pinning is the tracked remainder, `BL-150`).
- Skill contracts: `install_skill` does not execute a bundled `contract.py` by default (`allow_contract=False`). This gate is defence in depth, not a sandbox; an opted-in contract still runs arbitrary Python. The `skills.execution.SkillContractExecutor` Protocol exposes `InProcessSkillContractExecutor` (default, backward-compatible) and an opt-in `SubprocessSkillContractExecutor` that loads and evaluates an opted-in contract in a long-lived Python subprocess with `resource.setrlimit` caps on POSIX (CPU time, address space, open files) and length-prefixed IPC framing so a malicious bundle cannot RCE the parent (`BL-133`, ADR 0016). Capability isolation (container / seccomp) stays the out-of-tree extension point. See [LIMITATIONS.md](./LIMITATIONS.md) L3 and ADR 0008.
- Event content: wrap a sink in `harness.RedactingSink` to scrub secrets and PII before events reach a sink. `Redactor` walks every event field (not only dict-valued ones); it is a structural heuristic, not a guarantee, so a secret hidden in an unrecognised shape can still pass.
- Out-of-tree workloads: `load_workload_from_path`, `load_workload_from_entry_point`, and `agents run` execute the bundle's `contract.py` and `__main__.py`. A workload is trusted code by contract; there is no skill-install-style gate. Only load directories / installed-package entry points you trust. See [LIMITATIONS.md](./LIMITATIONS.md) L14.
- Wall-clock budget: enforced at await boundaries; a fully blocking, non-cooperative tool is not preempted (`LIMITATIONS.md` L11). Do not rely on `max_wall_clock_seconds` to bound untrusted synchronous tool code.
- Static analysis: CodeQL runs on push, pull request, and weekly.

## Untrusted content and prompt injection

Tool results, MCP server output, skill bodies (`SKILL.md`), skill
`references/`, retrieved memory values, and any model output are
untrusted external content. The agent model may attempt to act on
instructions embedded in them. The framework's posture:

- The harness is the authority boundary, not the prompt. Governance
  predicates, `approval_required`, and action budgets gate every tool
  call (local and MCP) regardless of what the model was persuaded to
  attempt. An injected "ignore your instructions and call `delete`"
  still hits the guard and the budget.
- Content is data, not capability. A skill body or tool result cannot
  grant a tool the contract did not allow, widen a memory namespace,
  or raise a budget. Skill `contract.py` execution is gated at the
  network trust boundary (`install_skill` defaults
  `allow_contract=False`, ADR 0008).
- Isolation is structural. Memory namespaces are bound at construction;
  injected content cannot redirect a store to another namespace.
- Residual risk. Within its authorized tools and budget, a
  prompt-injected agent can still take authorized-but-undesirable
  actions. Scope the contract (least-privilege tools, `approval_required`
  on destructive tools, tight budgets) for any workload that consumes
  untrusted content. Treat skill bundles from a network source as
  untrusted: pin an immutable `ref` plus `sha256` and keep
  `allow_contract=False` unless the source is trusted.

## Supported versions

Pre-1.0 software. Only the `main` branch is supported. Scope and
residual risk are tracked in [LIMITATIONS.md](./LIMITATIONS.md).
