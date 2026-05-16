# docs/schema/

Generated JSON Schema artifacts. Do not edit by hand.

- `workload-manifest.json` — JSON Schema for `manifest.yaml` (`WorkloadManifest`).
- `skill-manifest.json` — JSON Schema for SKILL.md frontmatter (`SkillManifest`).

Regenerate after changing the models: `make schema` (or
`python scripts/gen_schema.py`). CI and the test suite run
`python scripts/gen_schema.py --check`, which fails if these files drift
from the models. Editors can reference these for autocomplete and
inline validation. See ADR 0007 (BL-013).
