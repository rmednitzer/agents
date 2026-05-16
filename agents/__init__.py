"""agents: operator CLI for the workload/skill/harness/memory substrate.

`python -m agents <command>` is the entry point. Commands:

- ``workloads list`` — every loadable workload bundle (BL-020).
- ``skills list`` — every skill under ``skills/``, grouped by lane
  (BL-022).
- ``run <workload> <query>`` — load a workload, optionally dispatch a
  skill, run it under contract, print the structured result (BL-021).

The CLI is a thin shell over the existing public APIs
(``workloads.load_workload``, ``skills.SkillRegistry``); it adds no
behaviour the libraries do not already expose.
"""

from agents.cli import main

__all__ = ["main"]
