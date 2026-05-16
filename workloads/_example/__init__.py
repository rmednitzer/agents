"""Example workload: markdown style validator.

Validates a markdown document against the repository's contract style
conventions:

- Must start with an H1 heading.
- Must not contain em-dashes ('—').
- Must not contain double-dashes ('--') outside HTML comments.

Demonstrates the workload bundle convention: manifest.yaml, contract.py,
__main__.py.
"""
