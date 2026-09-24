HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: dev up down test test-thorough lint fmt check

dev:            ## run locally with reload (needs uv)
	uv run uvicorn sixhops.app.main:create_app --factory --reload --host $(HOST) --port $(PORT)

up:             ## run in docker
	docker compose up --build

down:
	docker compose down

test:
	uv run pytest

test-thorough:  ## long property-test run
	HYPOTHESIS_PROFILE=thorough uv run pytest tests/core/test_undo_properties.py

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

check: lint test
