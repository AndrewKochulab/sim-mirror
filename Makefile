# SimMirror development commands. Run `make help` for the list.

PYTHON_DIRS := src tests scripts benchmarks examples
COVERAGE_MIN := 98

.PHONY: help install lint lint-python typecheck guards test coverage format generate

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync

lint: lint-python typecheck guards ## Every static check

lint-python: ## Ruff lint and format check
	uv run ruff check $(PYTHON_DIRS)
	uv run ruff format --check $(PYTHON_DIRS)

typecheck: ## mypy --strict over the package and the scripts
	uv run mypy

guards: ## Containment, host-neutral vocabulary, license headers, distribution files and generated files
	uv run python scripts/check_containment.py
	uv run python scripts/check_host_neutral.py
	uv run python scripts/check_license_headers.py
	uv run python scripts/check_distribution.py
	sh scripts/check_generated.sh

generate: ## Rewrite every generated file from its source
	uv run python scripts/gen_protocol.py

test: ## Run the tests
	uv run pytest -q

coverage: ## Run the tests with the total and per-file coverage gates
	uv run pytest -q --cov --cov-report=term-missing:skip-covered --cov-report=json
	uv run python scripts/check_per_file_coverage.py --min $(COVERAGE_MIN) coverage.json

format: ## Format the code
	uv run ruff format $(PYTHON_DIRS)
	uv run ruff check --fix $(PYTHON_DIRS)
