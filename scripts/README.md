# scripts/

Operational and developer scripts. Bash and Python. Each script has a header comment explaining purpose and usage.

- `gen_schema.py`: regenerate `docs/schema/*.json` from the Pydantic
  models (`make schema`). `--check` is side-effect-free and is run by
  the test suite as a drift guard.
