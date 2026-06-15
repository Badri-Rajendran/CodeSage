.PHONY: help install dev up down logs test lint fmt typecheck initdb mcp clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime deps
	pip install -e .

dev:  ## Install dev deps
	pip install -e ".[dev]"

up:  ## Start the full stack (api + db) in Docker
	docker compose up --build

down:  ## Stop the stack
	docker compose down

logs:  ## Tail api logs
	docker compose logs -f api

test:  ## Run the test suite
	pytest -q

lint:  ## Lint with ruff
	ruff check app tests scripts

fmt:  ## Auto-format with ruff
	ruff check --fix app tests scripts
	ruff format app tests scripts

typecheck:  ## Static type check
	mypy app

initdb:  ## Create tables + pgvector extension
	python -m scripts.init_db

mcp:  ## Run the MCP server (stdio)
	python -m app.mcp.server

clean:  ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__
