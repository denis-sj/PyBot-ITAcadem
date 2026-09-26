#!/bin/sh
set -eu

export PYBOT_TEST_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:?POSTGRES_USER must be set}:${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}@test-postgres:5432/${POSTGRES_DB:?POSTGRES_DB must be set}_test"
sh scripts/ci/ensure_compose_test_database.sh
uv run pytest --cov=src/pybot --cov-report=term-missing --cov-report=xml --cov-fail-under=80
