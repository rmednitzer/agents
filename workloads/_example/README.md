# `_example` workload: markdown style validator

Demonstrates the workload bundle convention defined in
`docs/adr/0005-workload-bundles.md`. The workload validates a markdown
document against the repository's contract style rules and reports
findings.

## Bundle contents

- `manifest.yaml`: declares the workload to `load_workload`.
- `contract.py`: input / output Pydantic models, three predicates, one
  `Contract` instance.
- `__main__.py`: in-process stub runtime + CLI entry point.
- `README.md`: this file.

## What it checks

Per the repo's contract style:

- The document starts with an H1 header (`# Title`).
- The document contains no em-dashes (`—`).
- The document contains no double-dashes outside HTML comments.

## Running

```
python -m workloads._example path/to/document.md
```

Or via stdin:

```
cat document.md | python -m workloads._example
```

Output is a JSON ValidationReport with `passed`, `document_name`, and
`findings`.

## Testing

The bundle's contract is exercised by `tests/workloads/test_example.py`.
The harness's `run_under_contract` enforces the preconditions and
postconditions; this workload is the L1 reference for that integration.
