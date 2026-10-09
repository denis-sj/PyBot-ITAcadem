set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

default:
    @just --list

help: # Show available commands
    @just --list

install: # Install production dependencies
    uv sync

install-dev: # Install all dependencies, including dev groups
    uv sync --all-groups

run: # Run full local runtime via Docker Compose
    docker compose up --build

run-parity: # Run the official local dev/prod-like parity path
    $env:HEALTH_API_ENABLED='true'; docker compose --profile health up --build

run-health: # Backward-compatible alias for the parity path
    just run-parity

run-observability: # Run local runtime together with opt-in observability profile
    docker compose --profile observability up --build

format: # Format code with ruff
    uv run ruff format .

format-check: # Check formatting with ruff
    uv run ruff format --check --diff .

lint: # Run linter
    uv run ruff check .

style: # Run formatting check and lint and type check
    just format-check
    just lint
    just type-check

arch-check: # Check architecture invariants with Tach
    uv run --frozen tach check

test-coverage: # Run tests with coverage and show missing lines
    uv run pytest --cov=src/pybot --cov-report=term-missing --cov-report=xml --cov-fail-under=80

test-unit: # Run tests that do not require PostgreSQL or Docker
    uv run pytest -m "not integration"

test-integration: # Run PostgreSQL integration tests
    uv run pytest -m integration

quality-gate: # Full code quality gate (format check + lint + type check + arch check)
    just format-check
    just lint
    just type-check
    just arch-check
    just test-coverage

# Docker-based tooling runners for Linux and host Python drift scenarios.
test-unit-docker: # Run unit tests inside the tooling compose runner
    docker compose --profile tooling run --rm --build test-unit

test-integration-docker: # Run integration tests inside the tooling compose runner
    docker compose --profile tooling run --rm --build test-integration

test-coverage-docker: # Run coverage tests inside the tooling compose runner
    docker compose --profile tooling run --rm --build test-coverage

quality-gate-docker: # Run the full quality gate inside the tooling compose runner
    docker compose --profile tooling run --rm --build quality-gate

docs-install: # Install optional documentation dependencies
    uv sync --extra docs

docs-build: # Build MkDocs documentation in strict mode
    uv run --extra docs mkdocs build -f docs-project/mkdocs.yml --strict

docs-serve: # Serve MkDocs documentation locally
    uv run --extra docs mkdocs serve -f docs-project/mkdocs.yml

type-check: # Run type checker (ty)
    uv run ty check --python=.venv/ --output-format github --target-version 3.14 src/ tests/

migrate-create msg: # Create Alembic migration: just migrate-create "add new column"
    uv run alembic revision --autogenerate -m "{{msg}}"

migrate-apply: # Apply all Alembic migrations
    uv run alembic upgrade head

clean: # Remove local caches and venv
    uv run python -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for p in [Path('.venv'), Path('.ruff_cache'), Path('.mypy_cache'), Path('.pytest_cache')]]; [shutil.rmtree(p, ignore_errors=True) for p in Path('.').rglob('__pycache__')]"
    uv cache clean

pre-commit: # Install and run pre-commit hooks
    uv sync --all-groups
    uv run pre-commit install
    uv run pre-commit run --all-files

eval-agent: # Run opt-in live Polza evaluations (paid model requests, not CI)
    uv run python evals/agent/run.py
