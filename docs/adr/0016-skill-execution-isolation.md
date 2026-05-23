# ADR 0016: Skill contract execution isolation

- Status: Accepted
- Date: 2026-05-23
- Authors: rmednitzer
- Builds on: ADR 0001-0015

## Context

A skill bundle may ship a `contract.py` exporting `contract: Contract`
(BL-052). The harness composes this with the workload contract for
end-to-end behavioural enforcement. Loading `contract.py` executes
arbitrary Python from the bundle, so since BL-112 / ADR 0008
`install_skill` has defaulted to `allow_contract=False`: a present
`contract.py` is refused rather than executed. `LIMITATIONS.md` L3
records the open gap: "the gate is defence in depth, not a sandbox;
an opted-in contract still runs arbitrary Python."

`BL-133` is the open backlog item for true isolation. The runtime
contract is to execute `contract.py` with bounded authority: a
malicious or buggy bundle must not be able to crash the harness or
starve it of resources; ideally it should also be denied filesystem
and network access. Without isolation, opting in to `contract.py`
execution requires trusting the source completely.

The design decision spans three axes:

1. **Isolation mechanism**: in-process (no isolation), subprocess
   (crash + resource via `resource.setrlimit`), container
   (filesystem / network / syscall isolation via Docker / podman /
   seccomp / namespaces).
2. **Trust boundary**: where does the framework draw the line between
   "trusted enough to run" and "trusted enough to share the
   interpreter"?
3. **In-tree vs out-of-tree**: which mechanisms ship as in-tree
   references vs Protocol extension points?

The framework's recurring pattern (ADR 0001, ADR 0011, ADR 0006)
ships a Protocol plus a minimal reference, with non-trivial / vendor-
bound implementations out-of-tree. `KeyProvider` (static + env / file
+ versioned in-tree; KMS out-of-tree); `Embedder` (hashing in-tree;
vendor-quality out-of-tree); `SkillSource` (Local + GitHub +
generic Marketplace in-tree; deployment-specific marketplaces
out-of-tree). The same shape applies here.

## Decision

A new Protocol `skills.execution.SkillContractExecutor` decides *how*
a skill's `contract.py` is loaded and evaluated. The Protocol is
additive (ADR 0007): every existing caller continues to use the
in-process default and behaves exactly as before.

### Protocol

```python
class SkillContractExecutor(Protocol):
    def load(self, skill: Skill) -> Contract[Any, Any] | None: ...
```

Implementations:

- Return `None` when `skill.contract_path is None` (no `contract.py`
  to load).
- Raise `SkillManifestError` for an unparseable / mis-shaped
  `contract.py` (matching the documented L1 contract).
- Raise `SkillContractExecutorError` for an isolation-layer failure
  that is not the contract's fault (subprocess crashed, IPC framing
  broke, resource budget exhausted before load).
- Return a `Contract` (real or proxy) on success. The returned object
  is interoperable with `harness.compose_contracts` and with the
  `Predicate` Protocol; the harness does not need to know whether
  the predicates run in-process or via IPC.

### Reference implementations (in-tree)

**`InProcessSkillContractExecutor`** (default, backward-compatible):
delegates to the long-standing `skills.loader._load_skill_contract`,
which loads `contract.py` in this process via `importlib`. Trust
required: full. This is the L1 behaviour preserved unchanged.

**`SubprocessSkillContractExecutor`**: load and evaluate every
predicate in a long-lived Python subprocess.

- The child is spawned via `subprocess.Popen([sys.executable, "-m",
  "skills._executor_child"])` with the contract path and resource
  limits passed through the environment.
- The child applies `resource.setrlimit(RLIMIT_CPU, RLIMIT_AS,
  RLIMIT_NOFILE)` (POSIX) before importing `contract.py`.
- The child imports the contract module, serialises its
  metadata (`name`, `version`, predicate `name + severity` per
  slot) to a single JSON frame, ships it to the parent, and enters
  an evaluation loop.
- IPC framing: 4-byte big-endian length prefix + body. Parent->child
  bodies are pickled `(request, state)` tuples (parent owns the
  source; a malicious payload would just become the child's own
  problem, no worse than what `contract.py` already runs). Child->
  parent bodies are JSON `{"ok": bool}` / `{"error": str}` (never
  arbitrary pickled objects), so a malicious `contract.py` cannot
  RCE the parent through this channel.
- The parent constructs a `Contract` carrying `_PredicateProxy`
  predicates whose `__call__(state)` ships the state over IPC and
  returns the bool / wraps the exception in
  `SkillContractExecutorError`.
- `timeout_seconds` bounds every IPC round-trip; a hung child does
  not wedge the harness.

Crash isolation is real: a `contract.py` that segfaults,
`sys.exit`s, or stack-overflows raises
`SkillContractExecutorError` in the parent. Resource exhaustion is
bounded by `setrlimit`. Capability isolation (filesystem, network,
syscalls) is **NOT** enforced by this executor: the child inherits
the parent's namespace.

### Out-of-tree extension point

A deployment that needs filesystem / network / syscall isolation
supplies a custom `SkillContractExecutor`. Container-based,
seccomp-based, namespace-based, or platform-specific (Landlock,
AppArmor) implementations are out-of-tree by the ADR 0001 no-vendor-
binding stance: the Protocol is the in-tree extension point. The
container implementation is typically deployment-specific (Docker
vs podman vs k8s pod) and benefits from the operator's existing
container infrastructure, not a duplicated in-tree one.

### Wiring

- `Skill._executor: Any = None` (per-skill executor reference).
- `Skill.contract()` uses `self._executor.load(self)` when
  `_executor is not None`; falls through to the legacy
  `_load_skill_contract` otherwise.
- `discover_skill(executor=None)` and `install_skill(executor=None)`
  forward the executor to the constructed Skill.
- Defaults preserve every existing call site's behaviour.

### Trust framework

The combination of `allow_contract` and `executor` defines the
trust posture explicitly:

| `allow_contract` | `executor`            | Trust posture                          |
| ---------------- | --------------------- | -------------------------------------- |
| False (default `install_skill`) | (any)        | `contract.py` not executed; refused.   |
| True (default `discover_skill`) | None (default) / `InProcess` | Full trust: arbitrary Python in this interpreter. |
| True             | `Subprocess`          | Reduced trust: bundle may read fs / network, but cannot crash or starve the harness. |
| True             | (out-of-tree container) | Strong isolation: capability-bounded. |

An operator picks the executor that matches the source's trust
level; the default tier (no executor) preserves L1.

## Consequences

Backward compatibility: every test from the prior 833-test suite
passes unchanged (the in-process default at `Skill._executor=None`
is the legacy code path). The new executor is opt-in.

`LIMITATIONS.md` L3 is updated: the gate is no longer "defence in
depth, no isolation" but "defence in depth + an opt-in
SubprocessSkillContractExecutor with crash + rlimit isolation". The
remaining gap (capability isolation, container-level boundaries) is
the documented out-of-tree extension point.

Tests added (`tests/skills/test_bl133_execution_isolation.py`, 12
tests): Protocol smoke check; `InProcess` returns None when no
contract.py; `InProcess` loads and evaluates correctly;
`Subprocess` ditto with metadata propagation and IPC round-trips;
`Subprocess` translates import failure to `SkillManifestError`;
ditto missing-export; predicate-raise surfaces as
`SkillContractExecutorError`; load-crash surfaces as
`SkillManifestError` / `SkillContractExecutorError`;
`discover_skill(executor=...)` and `install_skill(executor=...)`
forward correctly; default Skill uses in-process (regression guard).

`make check` passes; 845 tests (+12 new); mypy strict clean over 73
source files; ruff / format clean; coverage 94.96%; schema clean;
evaluation gate PASS; REUSE 3.x compliant.

### Revisit triggers

- A vendor-quality container executor lands out-of-tree and an
  operator publishes it under a stable name: consider adding a
  documentation pointer (not a dep) to `skills/README.md`.
- A `seccomp` / Landlock / AppArmor profile becomes the deployment
  norm in this repo's downstream consumers: revisit the Protocol's
  surface to expose a "supported capabilities" introspection
  method, today absent.
- A real malicious bundle is observed evading the subprocess +
  rlimit boundary in the wild (CPU-evasion via fork bombs that
  ignore RLIMIT_NPROC, etc.): revisit the rlimit set; consider
  adding `RLIMIT_NPROC` to the child's limits as a third bound.
