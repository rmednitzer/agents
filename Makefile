.PHONY: help install lint format type-check test check ci schema clean

help:
	@echo "Targets:"
	@echo "  install    install dependencies (dev extras)"
	@echo "  lint       ruff check"
	@echo "  format     ruff format"
	@echo "  type-check mypy"
	@echo "  test       pytest"
	@echo "  check      lint + type-check + test"
	@echo "  ci         same as check (for parity with CI)"
	@echo "  schema     regenerate docs/schema/*.json from the models"
	@echo "  clean      remove caches and build artifacts"

install:
	uv sync --all-extras

lint:
	uv run ruff check .

format:
	uv run ruff format .

type-check:
	uv run mypy agents harness memory workloads skills evaluation

test:
	uv run pytest

check: lint type-check test

ci: check

schema:
	uv run python scripts/gen_schema.py

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
