# --- Stage 1: install dependencies into a virtual-env -----------------
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

# 1. Lock-файлы копируются первыми — слой инвалидируется
#    ТОЛЬКО при изменении зависимостей, не кода.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. Исходный код и runtime-файлы копируются отдельным слоем.
COPY src ./src
COPY README.md ./
COPY run.py ./
COPY fill_point_db.py ./
COPY alembic ./alembic
COPY alembic.ini ./

# 2.1 Инсталлируем сам проект (создает исполняемый файл pybot-seed и добавляет src в sys.path)
RUN uv sync --frozen --no-dev

# --- Stage 1.5: install dev tooling into a dedicated virtual-env ------
FROM python:3.14-slim AS tooling

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300 \
    UV_PROJECT_ENVIRONMENT=/opt/pybot-tooling/.venv \
    PATH="/opt/pybot-tooling/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends git postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-groups --no-install-project

COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY README.md ./
COPY run.py ./
COPY fill_point_db.py ./
COPY alembic ./alembic
COPY alembic.ini ./

RUN uv sync --frozen --all-groups

# --- Stage 2: lean runtime image -------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

# 3. venv копируется отдельно от кода — при изменении только кода
#    этот слой остаётся закэшированным.
COPY --from=builder /app/.venv /app/.venv

# 4. Application code и runtime-файлы.
COPY --from=builder /app/src /app/src
COPY --from=builder /app/run.py /app/run.py
COPY --from=builder /app/fill_point_db.py /app/fill_point_db.py
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini /app/alembic.ini

RUN mkdir -p /app/data && chown -R app:app /app

USER app

CMD ["python", "run.py"]
