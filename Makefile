# SimMirror development commands. Run `make help` for the list.

.PHONY: help install lint test coverage format

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync

lint: ## Every static check
	uv run ruff check src tests
	uv run ruff format --check src tests

test: ## Run the tests
	uv run pytest -q

coverage: ## Run the tests with the coverage gate
	uv run pytest -q --cov --cov-report=term-missing

format: ## Format the code
	uv run ruff format src tests
	uv run ruff check --fix src tests
