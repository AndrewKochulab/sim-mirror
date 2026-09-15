# SimMirror development commands. Run `make help` for the list.

PYTHON_DIRS := src tests scripts benchmarks examples
COVERAGE_MIN := 98
VIEWER := viewer

.PHONY: help install lint lint-python typecheck guards lint-viewer test test-python test-viewer coverage \
	coverage-python coverage-viewer viewer-bundle format generate

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the Python and viewer dependencies
	uv sync
	cd $(VIEWER) && npm ci

lint: lint-python typecheck guards lint-viewer ## Every static check

lint-python: ## Ruff lint and format check
	uv run ruff check $(PYTHON_DIRS)
	uv run ruff format --check $(PYTHON_DIRS)

typecheck: ## mypy --strict over the package, the scripts, the benchmark and the examples
	uv run mypy

guards: ## Containment, host-neutral vocabulary, license headers, distribution files, links, media and generated files
	uv run python scripts/check_containment.py
	uv run python scripts/check_host_neutral.py
	uv run python scripts/check_license_headers.py
	uv run python scripts/check_distribution.py
	uv run python scripts/check_links.py
	uv run python scripts/check_media_sizes.py
	sh scripts/check_generated.sh

lint-viewer: ## ESLint and tsc over the viewer
	cd $(VIEWER) && npm run lint && npm run typecheck

generate: ## Rewrite every generated file from its source
	uv run python scripts/gen_protocol.py
	uv run python scripts/gen_docs.py

test: test-python test-viewer ## Run the Python and viewer tests

test-python: ## Run the Python tests
	uv run pytest -q

test-viewer: ## Run the viewer tests
	cd $(VIEWER) && npm test

coverage: coverage-python coverage-viewer ## Run every test with the total and per-file coverage gates

coverage-python: ## Python tests with the total and per-file coverage gates
	uv run pytest -q --cov --cov-report=term-missing:skip-covered --cov-report=json
	uv run python scripts/check_per_file_coverage.py --min $(COVERAGE_MIN) coverage.json

coverage-viewer: ## Viewer tests with the per-file coverage thresholds
	cd $(VIEWER) && npm run coverage

viewer-bundle: ## Rebuild the viewer, then check the committed page bundle and the size budget
	cd $(VIEWER) && npm run build
	uv run --no-project python scripts/check_viewer_bundle.py

format: ## Format the code
	uv run ruff format $(PYTHON_DIRS)
	uv run ruff check --fix $(PYTHON_DIRS)
