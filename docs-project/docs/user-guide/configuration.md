# Конфигурация

Настройки приложения определены в [`pybot.core.config.AppSettings`](../api-reference/core.md). Значения materialize-ятся через `get_settings()`: bootstrap явно подхватывает локальный `.env`, а сама модель не зашита на конкретный источник.

`AppSettings` описывает только runtime-конфигурацию Python-приложения. Deploy-only и orchestration-переменные не materialize-ятся в эту модель и не валидируются приложением: их contract поддерживают `docker-compose*`, GitHub Actions и Ansible.

## Обязательные переменные

| Переменная | Назначение |
| --- | --- |
| `BOT_TOKEN` | production-токен бота |
| `BOT_TOKEN_TEST` | тестовый токен; обязателен при `BOT_MODE=test`, опционален при `BOT_MODE=prod` |
| `BOT_MODE` | режим `test` или `prod` |
| `ROLE_REQUEST_ADMIN_TG_ID` | Telegram ID администратора для role request |
| `POSTGRES_DB` | имя PostgreSQL базы |
| `POSTGRES_USER` | пользователь PostgreSQL |
| `POSTGRES_PASSWORD` | raw-пароль PostgreSQL |
| `DATABASE_URL` | `postgresql+asyncpg` URL; внутри Compose hostname должен быть `postgres` |

## Часто используемые параметры

| Переменная | Что регулирует |
| --- | --- |
| `LOG_LEVEL` | уровень логирования |
| `LOG_FORMAT` | формат stdout-логов: `text` или `json` |
| `DEBUG` | debug-режим |
| `FSM_STORAGE_BACKEND` | backend хранения FSM |
| `REDIS_URL` | Redis для FSM и TaskIQ |
| `NOTIFICATION_BACKEND` | `telegram` или `logging` |
| `TELEGRAM_PROXY_URL` | Необязательный proxy для Telegram Bot API. Поддерживаются `http://`, `socks4://`, `socks5://` и `socks5h://`; последний преобразуется в `socks5://` с удалённым DNS. Остальные схемы config-check отклоняет до запуска сервисов. |
| `RUNTIME_ALERTS_ENABLED` | включает runtime alerts для bot startup/shutdown |
| `RUNTIME_ALERTS_CHAT_ID` | chat id для runtime alerts |
| `HEALTH_API_ENABLED` | отдельный health API |
| `EVENTS_CHAT_ID` и `EVENTS_TOPIC_ID` | необязательная тема анонсов встреч всем; задайте обе переменные или оставьте обе пустыми |
| `EVENTS_TOPIC_NAME` | название темы из Telegram, показываемое в предпросмотре; по умолчанию `Встречи` |

Если обе переменные пусты, бот использует настроенную тему агента (`AGENT_GROUP_CHAT_ID` и `AGENT_TOPIC_ID`). Если не
настроена ни одна пара, создание встречи для всех отключено; личные приглашения по компетенции и стеку работают отдельно.

## Logging contract

`BOT_MODE` и `LOG_FORMAT` решают разные задачи:

- `BOT_MODE` выбирает runtime mode и активный bot token;
- `LOG_FORMAT` выбирает формат stdout-логов.

Рекомендуемый контракт такой:

- `just run-parity` / `HEALTH_API_ENABLED=true docker compose --profile health up --build` — официальный local dev/prod-like parity path, поэтому здесь используется `LOG_FORMAT=json`;
- `just run` / `docker compose up --build` — базовый local runtime без отдельного `health` process type, но с тем же machine-readable logging contract;
- production runtime тоже использует `LOG_FORMAT=json`;
- `uv run run.py` остаётся manual bot-only/debug path и может использовать `text`, если `LOG_FORMAT` не задан явно.

То есть `BOT_MODE=test` не означает автоматически, что любой локальный запуск обязан использовать `text`. Для Compose-based local runtime формат логов задаётся отдельно и сознательно.

## Local observability profile

Локальный Compose также поддерживает opt-in profile `observability`.

- `just run` оставляет локальный runtime лёгким и поднимает только app-сервисы и Redis;
- `just run-observability` включает `loki`, `alloy`, `grafana` и `nginx` поверх того же runtime;
- local observability path reuse-ит конфиги из `observability/`, но не требует production host nginx или public HTTPS;
- локальный ingress считается HTTP-only и по умолчанию использует `http://127.0.0.1:8088/grafana/`.

Для этого сценария локальный Compose уже содержит безопасные defaults для Grafana/nginx, поэтому отдельный `.env` только ради observability-контура не нужен.

## Local health profile

Локальный Compose также поддерживает opt-in profile `health`.

- `just run` не поднимает отдельный health-process type;
- `just run-parity` включает `HEALTH_API_ENABLED=true` и запускает тот же отдельный `health` process type, который используется в production;
- `just run-parity` считается одним официальным local parity path для smoke-check и readiness, а `just run` остаётся более лёгким runtime-сценарием;
- `just run-health` остаётся совместимым alias для плавного перехода;
- локальный smoke-check идёт напрямую в health API порт, а не через production ingress path.

Каноническая локальная проверка:

- `GET http://127.0.0.1:8001/` — liveness, ожидается `200`;
- `GET http://127.0.0.1:8001/ready` — readiness, ожидается `200`, когда приложение готово.

## Deploy / Orchestration-переменные

Эти переменные не входят в `AppSettings` и не участвуют в app-side fail-fast. Их проверяют Compose и deploy automation.

К PostgreSQL-конфигурации применяются дополнительные правила:

- `POSTGRES_DB`, `POSTGRES_USER` и `POSTGRES_PASSWORD` обязательны для Compose;
- database, user и percent-decoded password в `DATABASE_URL` должны совпадать с `POSTGRES_*`;
- URL-special символы в password component `DATABASE_URL` нужно percent-encode, сохраняя raw-значение в `POSTGRES_PASSWORD`;
- `POSTGRES_PORT` управляет только локальным bind на `127.0.0.1`; production не публикует PostgreSQL port;
- host-only команды используют `127.0.0.1` вместо Compose hostname `postgres`;
- SQLite URL не поддерживаются приложением и Alembic.

`TASKIQ_WORKERS` управляет worker concurrency на уровне Compose:

- по умолчанию используется `${TASKIQ_WORKERS:-1}`;
- текущее поддерживаемое значение только `1`;
- значения больше `1` намеренно отклоняются fail-fast guard-ом.

Примеры:

```bash
TASKIQ_WORKERS=1 docker compose up --build
TASKIQ_WORKERS=1 docker compose -f docker-compose.prod.yml up -d
```

Конфигурации вида `TASKIQ_WORKERS=2 ...` в текущей системе не поддерживаются.

Observability/deploy-only переменные также остаются вне `AppSettings`:

- `GRAFANA_ADMIN_PASSWORD` — обязателен для production Grafana по contract `docker-compose.prod.yml`;
- `PUBLIC_DOMAIN` — production ingress/domain value для host nginx и compose nginx;
- `NGINX_BIND_HOST` и `NGINX_PORT` — ingress binding для compose nginx;
- локальный observability profile имеет безопасные defaults и не требует этих значений для базового старта.

## Broadcast-настройки

В `AppSettings` также есть группа параметров для рассылок:

- `BROADCAST_BULK_SIZE`
- `BROADCAST_MAX_CONCURRENCY`
- `BROADCAST_BATCH_PAUSE_MS`
- `BROADCAST_JITTER_MIN_MS`
- `BROADCAST_JITTER_MAX_MS`
- `BROADCAST_RETRY_ATTEMPTS`
- `BROADCAST_RETRY_MAX_WAIT_S`

!!! tip "Практика"
    Для локальной разработки держите `BOT_MODE=test`, `FSM_STORAGE_BACKEND=redis` и PostgreSQL URL с Compose hostname `postgres`. Перед первым runtime-запуском поднимите `postgres` и `redis`, затем явно выполните migration profile. Если нужен только более быстрый базовый runtime без readiness-процесса, используйте `just run` / `docker compose up --build`.

`FSM_STORAGE_BACKEND=memory` остаётся поддерживаемым, но только как явный opt-in fallback для bot-only debug path. Если вы запускаете только `uv run run.py`, Redis должен быть уже доступен отдельно, если backend не переключён вручную на `memory`.

`TELEGRAM_PROXY_URL` можно оставить пустым. Переменная нужна только для окружений, где доступ к Telegram Bot API возможен через proxy.

`RUNTIME_ALERTS_ENABLED` и `RUNTIME_ALERTS_CHAT_ID` тоже являются опциональными. В v1 они покрывают только lifecycle основного `bot` runtime: startup-уведомление идет через TaskIQ, а shutdown-уведомление отправляется напрямую как best effort.
