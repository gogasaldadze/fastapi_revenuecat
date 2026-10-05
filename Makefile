PYTHON ?= python3.12
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help install lint format typecheck test cov check run migrate revision up down clean

help:
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install dev dependencies
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements_dev.txt

lint: ## Run ruff lint and format checks
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Apply ruff fixes and formatting
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Run mypy in strict mode
	$(BIN)/mypy

test: ## Run the test suite
	$(BIN)/pytest

cov: ## Run tests with coverage (fails under 90 percent)
	$(BIN)/pytest --cov --cov-report=term-missing

check: lint typecheck cov ## Everything CI runs

run: ## Start the API with reload on port 8000
	$(BIN)/uvicorn app.main:app --reload --port 8000

migrate: ## Apply database migrations
	$(BIN)/alembic upgrade head

revision: ## Create a migration from model changes, usage: make revision m="add column"
	$(BIN)/alembic revision --autogenerate -m "$(m)"

up: ## Start api, postgres and redis with docker compose
	docker compose up --build

down: ## Stop docker compose services
	docker compose down

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov
