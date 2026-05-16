---
name: example
description: Example reference skill demonstrating the Agent Skills specification. Use this skill as a template for new skills or when testing the loader, registry, and dispatcher code paths. Demonstrates frontmatter fields, metadata extensions (lane, triggers), and references directory layout.
license: Apache-2.0
metadata:
  lane: documentation
  triggers: example, template, reference
---

# Example skill

This is the body of the example skill. It demonstrates the SKILL.md
format defined by the Agent Skills open standard at
[agentskills.io](https://agentskills.io/specification).

## When to use

Use this skill as a template for new skills in the agents repository.
It exercises the loader, registry, and dispatcher code paths in tests
and serves as a documented baseline for skill authors.

## Body structure

Skills are encouraged to keep the SKILL.md body under 500 lines and
move detailed technical reference to files in references/. This skill
is intentionally short.

## Resources

A real skill might include:

- references/REFERENCE.md for detailed technical material.
- scripts/extract.py for executable helpers.
- assets/template.md for output templates.

This example ships none of those; the directories are absent.
