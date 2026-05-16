.PHONY: help install lint format type-check test check ci clean

help:
	@echo "Targets:"
	@echo "  install    install dependencies (dev extras)"
	@echo "  lint       ruff check"
	@echo "  format     ruff format"
	@echo "  type-check mypy"
	@echo "  test       pytest"
	@echo "  check      lint + type-check + test"
	@echo "  ci         same as check (for parity with CI)"
	@echo "  clean      remove caches and build artifacts"

install:
	uv sync --all-extras

lint:
	uv run ruff check .

format:
	uv run ruff format .

type-check:
	uv run mypy harness memory workloads skills

test:
	uv run pytest

check: lint type-check test

ci: check

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
