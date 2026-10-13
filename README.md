# PyBot ITAcadem

Асинхронный Telegram-бот для ITAcadem на `Python 3.14`, `aiogram 3`, `Dishka`, `SQLAlchemy 2` и `Pydantic v2`.

Сейчас проект закрывает не абстрактный "будущий MVP", а вполне конкретный набор сценариев:

- регистрация пользователя через Telegram-диалог;
- просмотр профиля с ролями, компетенциями и прогрессом по баллам;
- role request flow с подтверждением или отклонением администратором;
- админские команды для ролей, компетенций, баллов и рассылок;
- ИИ-агент: вопросы к боту обычным текстом в личке и в группе (в разработке, выключен по умолчанию);
- health/readiness API;
- Docker/runtime-контур с `Redis` и `TaskIQ`;
- production deploy skeleton через `docker-compose.prod.yml`, GitHub Actions и Ansible.

## Что реально есть в проекте сейчас

### Пользовательские сценарии

- `/start` в личном чате:
  - показывает профиль и главное меню кнопок, если пользователь уже зарегистрирован;
  - иначе запускает пошаговую регистрацию, а после неё показывает профиль с тем же меню.
- Главное меню (кнопки под профилем): «Профиль», «Посещаемость» (то же, что `/visits`), «Лидерборд», «Помощь»;
  администраторам, которым разрешены рассылки, добавляется «Рассылка всем» — бот просит текст, показывает
  подтверждение и только после него отправляет сообщение всем пользователям.
- Если незарегистрированный пользователь нажимает кнопку меню или отправляет `/visits` (в личном чате или в группе),
  `/profile` в группе — бот предлагает кнопку «Начать регистрацию»: в личном чате она запускает регистрацию,
  в группе открывает чат с ботом.
- Регистрация через `aiogram-dialog`:
  - запрос контакта;
  - имя;
  - фамилия;
  - отчество;
  - выбор компетенций с возможностью пропуска.
- `/profile` показывает:
  - академический уровень;
  - репутационный уровень;
  - баллы;
  - роли;
  - компетенции.
- `/help`, `/info`, `/ping` и `/competences` работают как пользовательские команды.
- `/showcompetences [@user|id|reply]` показывает компетенции конкретного пользователя.
- Учёт посещаемости и часов, команда `/visits` (работает в личном чате и в группе):
  - главная карточка показывает часы, число посещений и место в рейтинге за текущий месяц;
  - «Топ недели» — топ-5 по сумме часов за текущую неделю (понедельник–суббота);
  - «Топ месяца» — топ-5 за текущий месяц, стрелками можно перейти к прошлым месяцам;
  - «Мои посещения» — история подтверждённых посещений по месяцам с кнопками фото листа;
  - все экраны открываются в том же сообщении, карточка отвечает только тому, кто её открыл.

### Админские сценарии

- `/academic_points @user <число> "причина"` и `/reputation_points @user <число> "причина"` меняют баллы пользователя.
- `/addrole` и `/removerole` управляют ролями `Student`, `Mentor`, `Admin`.
- `/addcompetence` и `/removecompetence` управляют компетенциями пользователя.
- `/broadcast @all <текст>` рассылает сообщение всем.
- `/broadcast <Role> <текст>` рассылает по роли.
- `/broadcast <Competence> <текст>` рассылает по компетенции.
- Учёт посещаемости по фото (Polza.ai Vision):
  - кнопка «Добавить часы по фото» в меню `/report` для запуска загрузки;
  - отправка фото листа посещаемости в личный чат боту администратором;
  - AI-распознавание дат, участников и времени с автоматическим расчётом длительности и сопоставлением с базой пользователей;
  - строки таблицы определяются по линейкам выровненного листа: модель получает отдельное изображение каждой строки (печатное ФИО и только рукописная запись этой строки) и лишь читает его, поэтому не может сдвинуть время или ФИО на соседнюю строку; если запись пересекает границу строк неоднозначно, обе строки требуют ручной проверки, а неполное время (например, «8:40-») сохраняется как на листе и требует ввода конца;
  - строки отсутствия без времени отображаются как `N` и не сохраняются как посещения;
  - интерактивное редактирование черновика (изменение времени, назначение участника, исключение строк);
  - подтверждение импорта с защитой от дубликатов дат и пользователей;
  - `/current_month` — просмотр текущего расчётного месяца учёта;
  - `/report` — генерация краткого или подробного отчёта по посещаемости клуба за месяц;
  - `/attendance_photo` — выбор месяца и записанной даты для просмотра оригинального фото; также поддерживается `/attendance_photo <номер>`.

### Role request flow

- `/role_request <Student|Mentor|Admin>` создаёт запрос на роль.
- Администратор получает уведомление с inline-кнопками одобрения и отклонения.
- Есть защита от повторных активных запросов и cooldown после отклонения.

### ИИ-агент (опциональная функция)

> Функция развивается в ветке `feat/ai-agent-harness` и выключена по умолчанию (`AGENT_ENABLED=false`).
> Реализованы чтение данных и действия с подтверждением; агент выключен по умолчанию.

Пользователь пишет боту обычным текстом — «сколько у меня очков?», «кто в топе посещений за неделю?» — агент сам
выбирает нужную функцию бота, выполняет её с правами этого пользователя и отвечает reply на сообщение.

- **Где отвечает:** в личке — на любой текст; в специальном топике группы упоминать бота необязательно, упоминания
  других людей допустимы; в остальных топиках —
  только на `@бот …` или reply на ответ агента, если метка ответа ещё не истекла. По умолчанию метка живёт 7 дней
  (`AGENT_REPLY_MARK_TTL_S`). Команды, фото и другие сценарии (регистрация, текст рассылки) обрабатываются как раньше.
- **Что умеет сейчас:** профиль и баллы, мои посещения, топ посещаемости недели и месяца, лидерборд,
  роли и компетенции, информация о боте; администраторам — текущий месяц учёта, отчёт по клубу,
  профиль и посещения другого участника, ID чата; также заявка на роль, изменения баллов, ролей, компетенций и
  посещений, а также рассылка для ролей из `BROADCAST_ALLOWED_ROLES`.
- **Подтверждение:** перед изменением данных или рассылкой агент показывает действие, аудиторию и текст, затем
  ждёт «Да» или «Отмена». Нажать кнопку может только автор запроса; рассылка через агента доступна в личном чате.
- **Безопасность:** агент не даёт больше прав, чем команды; права проверяются в коде дважды — при выборе
  инструментов для модели и при выполнении. Цифры берутся только из базы.
- **Память:** последние 10 сообщений на человека в каждом чате, 30 минут, в Redis; срок меток ответов настраивается
  отдельно и по умолчанию составляет 7 дней.

```mermaid
flowchart LR
    U["Пользователь:<br/>«сколько у меня очков?»"] --> B["Бот (aiogram):<br/>это ко мне? кто пишет? роли"]
    B --> H["Харнесс (Pydantic AI):<br/>инструменты по роли + память"]
    H <--> M["Gemini 3.5 Flash-Lite<br/>через Polza"]
    H --> T["Инструмент get_my_profile:<br/>повторная проверка прав"]
    T --> S["Тот же сервис, что /profile<br/>→ PostgreSQL"]
    S --> H
    H --> A["Ответ reply:<br/>«У тебя 120 академических<br/>и 35 репутационных баллов»"]
```

Подробно — с примерами диалогов, схемами, списком инструментов и настройками: [ИИ-агент](docs-project/docs/user-guide/ai-agent.md).

### Инфраструктурные возможности

- Отдельный FastAPI health API:
  - `GET /health` для liveness;
  - `GET /ready` для readiness с проверкой БД.
- Поддержка `Redis`:
  - как backend для FSM;
  - как broker/schedule backend для `TaskIQ`.
- Выделенные runtime-процессы:
  - `bot`;
  - `taskiq-worker`;
  - `taskiq-scheduler`.
- Seed-скрипт `fill_point_db.py` умеет наполнять роли, уровни, компетенции и фейковых пользователей.

## Что важно понимать про текущее состояние

- Проект уже не ограничивается только регистрацией и профилем: в коде есть рабочие сценарии для roles, competencies, broadcasts, notification runtime и health-check.
- В репозитории есть модели задач и решений, но пользовательский Telegram-flow для задач пока не является основным и не описывается как готовая публичная возможность.
- README ниже описывает именно то, что уже есть в кодовой базе сейчас, а не желаемое состояние "на будущее".

## Архитектура

Проект следует `Layered Architecture` с элементами pragmatic DDD.

- `src/pybot/presentation/bot/` - canonical presentation layer for Telegram handlers, dialogs, filters, middlewares, keyboards and runtime wiring.
- `src/pybot/presentation/texts/` - shared user-facing texts and message renderers.
- `src/pybot/services/` - application services и orchestration.
- `src/pybot/infrastructure/` - repositories, adapters, TaskIQ integration, внешние порты.
- `src/pybot/db/` - SQLAlchemy models и database setup.
- `src/pybot/dto/` - DTO и value objects.
- `src/pybot/domain/` - domain exceptions и domain services.
- `src/pybot/di/` - Dishka composition root.
- `src/pybot/presentation/web/` - web presentation API and health server.

Ключевые архитектурные решения зафиксированы в ADR:

- `008` - разделение `find_*` и `get_*` lookup semantics;
- `010` - ports and adapters для внешних интеграций;
- `011` - `TaskIQ + Redis` для фоновых задач и очередей.

## Технологический стек

- `Python 3.14+`
- `aiogram 3.22+`
- `aiogram-dialog 2.4+`
- `Dishka`
- `SQLAlchemy 2` + `PostgreSQL 18` + `asyncpg`
- `Alembic`
- `Pydantic v2` + `pydantic-settings`
- `Pydantic AI` + Polza (`gemini-3.5-flash-lite`) для ИИ-агента
- `Redis`
- `TaskIQ` + `taskiq-redis`
- `FastAPI` + `uvicorn`
- `loguru`
- `uv`
- `ruff`, `ty`, `pytest`, `pytest-aiogram`
- `MkDocs Material`

### Контракт базы данных

- Единственная поддерживаемая runtime БД — PostgreSQL 18.
- Приложение и Alembic принимают только URL вида `postgresql+asyncpg://...`.
- SQLite больше не поддерживается ни как runtime БД, ни как источник для обновления через текущую цепочку Alembic.
- Текущая Alembic baseline рассчитана на пустую PostgreSQL БД. Автоматического переноса существующей SQLite БД или старых revision ID нет.

## Структура репозитория

```text
PyBot_ITAcadem/
├── src/pybot/
│   ├── bot/                  # handlers, dialogs, middlewares, filters, keyboards
│   ├── core/                 # settings, enums, logger
│   ├── db/                   # SQLAlchemy setup и ORM models
│   ├── di/                   # Dishka containers
│   ├── domain/               # domain exceptions и domain services
│   ├── dto/                  # DTO и value objects
│   ├── health/               # FastAPI health app/server
│   ├── infrastructure/       # repositories, adapters, TaskIQ runtime
│   ├── mappers/              # layer mappers
│   ├── services/             # application services
│   └── utils/                # shared utils
├── tests/                    # unit, integration, bot, health, script tests
├── alembic/                  # migrations
├── docs-project/             # MkDocs documentation
├── ansible/                  # deploy/bootstrap playbooks
├── docker-compose.yml
├── docker-compose.prod.yml
├── fill_point_db.py
└── run.py
```

## Локальный запуск

### 1. Требования

- `Python 3.14+`
- `uv`
- `just` - желательно, но не обязательно
- `Docker` + `docker compose` - рекомендуемый локальный runtime-path с PostgreSQL 18, включая официальный parity path через `just run-parity`
- для запуска без Compose должны быть отдельно доступны PostgreSQL и Redis; Redis не нужен только при явном переключении `FSM_STORAGE_BACKEND=memory`

### 2. Установка зависимостей

```bash
git clone https://github.com/NikkiShuRA/PyBot-ITAcadem.git
cd PyBot-ITAcadem
uv sync --all-groups
```

Если нужна только основная среда без dev/doc extras:

```bash
uv sync
```

### 3. Настройте `.env`

Минимальный рабочий пример для локальной разработки:

```env
BOT_TOKEN=your_production_bot_token
BOT_TOKEN_TEST=your_test_bot_token
BOT_MODE=test

POSTGRES_DB=pybot_itacadem
POSTGRES_USER=pybot
POSTGRES_PASSWORD=change_me_before_run
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://pybot:change_me_before_run@postgres:5432/pybot_itacadem

ROLE_REQUEST_ADMIN_TG_ID=123456789
AUTO_ADMIN_TELEGRAM_IDS=

NOTIFICATION_BACKEND=telegram
TELEGRAM_PROXY_URL=
RUNTIME_ALERTS_ENABLED=false
RUNTIME_ALERTS_CHAT_ID=
FSM_STORAGE_BACKEND=redis
REDIS_URL=redis://redis:6379/0

LOG_LEVEL=INFO
LOG_FORMAT=json
DEBUG=false

HEALTH_API_ENABLED=false
HEALTH_API_HOST=127.0.0.1
HEALTH_API_PORT=8001
```

Что важно:

- `AppSettings` materialize-ятся через `get_settings()`, который явно подхватывает локальный `.env` на bootstrap-этапе;
- `AppSettings` описывает только runtime-настройки приложения; deploy-only переменные вроде `TASKIQ_WORKERS`, `GRAFANA_ADMIN_PASSWORD`, `PUBLIC_DOMAIN` и `NGINX_*` валидируются Compose/CI/CD, а не Python-приложением;
- `BOT_TOKEN_TEST` обязателен при `BOT_MODE=test` и опционален при `BOT_MODE=prod`;
- `BOT_MODE=test` использует `BOT_TOKEN_TEST`, `BOT_MODE=prod` использует `BOT_TOKEN`;
- `BOT_MODE` отвечает за runtime mode и выбор активного bot token, а `LOG_FORMAT` — за формат stdout-логов; это две независимые настройки;
- `ROLE_REQUEST_ADMIN_TG_ID` обязателен, потому что role request flow уже является частью рабочего сценария;
- `TELEGRAM_PROXY_URL` опционален и нужен только там, где Telegram Bot API доступен через proxy;
- `RUNTIME_ALERTS_ENABLED` и `RUNTIME_ALERTS_CHAT_ID` опциональны и включают runtime alerts только для основного bot-процесса;
- локальный и production Compose используют PostgreSQL 18 и обращаются к нему по service hostname `postgres`;
- `POSTGRES_DB`, `POSTGRES_USER` и `POSTGRES_PASSWORD` обязательны, а `POSTGRES_PORT` задаёт только локальный bind на `127.0.0.1`;
- database name, user и password в `DATABASE_URL` должны совпадать с `POSTGRES_DB`, `POSTGRES_USER` и `POSTGRES_PASSWORD`;
- `POSTGRES_PASSWORD` хранит raw-пароль, но URL-special символы в password component `DATABASE_URL` должны быть percent-encoded;
- для host-only команд временно замените hostname `postgres` в `DATABASE_URL` и hostname `redis` в `REDIS_URL` на `127.0.0.1`; внутри Compose оставляйте service hostnames;
- локальный dev-default теперь использует `FSM_STORAGE_BACKEND=redis`, чтобы bot, worker и scheduler работали на том же Redis runtime;
- официальный dev/prod-like parity path через `just run-parity` / Docker Compose использует `LOG_FORMAT=json` как prod-like logging contract;
- ручной bot-only запуск через `uv run run.py` может оставаться на `text`, если `LOG_FORMAT` не задан явно; это осознанный debug/DX trade-off, а не drift;
- `FSM_STORAGE_BACKEND=memory` остаётся только как явный opt-in fallback/debug path;
- если вы запускаете только bot process через `uv run run.py`, Redis должен быть уже доступен отдельно, если backend не переключён вручную на `memory`.

### 4. Выполните clean start PostgreSQL runtime

```bash
docker compose up -d --wait postgres redis
docker compose --profile migration run --rm migrate
docker compose --profile seed run --rm seed
docker compose up --build
```

`seed` в этой последовательности опционален. Обычный `docker compose up` не запускает ни миграции, ни seed автоматически: `migrate` выполняют явно перед первым стартом и после изменений схемы, а `seed` — только когда нужны initial/test данные. Для полного сброса локальной БД удалите Compose volume только осознанно: это необратимо удалит все локальные PostgreSQL-данные.

Для host-only запуска Alembic PostgreSQL должен быть опубликован локальным Compose на `127.0.0.1:${POSTGRES_PORT:-5432}`. Временно замените hostname в URL:

```bash
DATABASE_URL=postgresql+asyncpg://pybot:change_me_before_run@127.0.0.1:5432/pybot_itacadem uv run alembic upgrade head
```

### 5. При необходимости заполните БД тестовыми данными

```bash
uv run python fill_point_db.py --help
uv run python fill_point_db.py
```

Это локальный эквивалент one-shot process type `seed`. Он не считается частью обычного runtime-старта и запускается только тогда, когда нужен initial/test seed. В Docker Compose этому соответствует `docker compose --profile seed run --rm seed`.

Seed не является полностью атомарным: отдельные шаги и сервисы выполняют
промежуточные `commit()`. При ошибке CLI откатывает только текущую
незавершённую транзакцию и завершается с ненулевым кодом, но ранее
зафиксированные изменения могут остаться в БД.

Скрипт умеет отдельно включать и отключать:

- уровни;
- роли;
- компетенции;
- фейковых пользователей.

### 6. Официальный локальный parity path

Если нужен один локальный сценарий, который лучше всего повторяет production process model, используйте именно его:

```bash
docker compose up -d --wait postgres redis
docker compose --profile migration run --rm migrate
just run-parity
curl -i http://127.0.0.1:8001/
curl -i http://127.0.0.1:8001/ready
```

Это официальный dev/prod-like path, потому что он:

- использует тот же Compose-based runtime;
- поднимает те же core process types, что и production: `bot`, `taskiq-worker`, `taskiq-scheduler`, `postgres`, `redis`;
- добавляет отдельный `health` process type через profile, как и production deploy;
- проверяет readiness приложения, а не только факт старта контейнеров.

### 7. Другие локальные entrypoint'ы

```bash
just run
```

`just run` теперь использует `docker compose up --build` и поднимает базовый локальный runtime:

- `bot`
- `taskiq-worker`
- `taskiq-scheduler`
- `postgres`
- `redis`

Это быстрый local runtime path без отдельного `health` process type.

Официальный parity path поднимается отдельно:

```bash
just run-parity
```

`just run-parity` включает `HEALTH_API_ENABLED=true` и запускает тот же отдельный `health` process type через Compose profile, что и production deploy. `just run-health` остаётся совместимым alias. После старта можно проверить:

```bash
curl -i http://127.0.0.1:8001/
curl -i http://127.0.0.1:8001/ready
```

Успешный сценарий: liveness endpoint отвечает `200`, а readiness endpoint тоже доходит до `200`, когда приложение реально готово обслуживать трафик.

Расширенный local observability path поднимается отдельно:

```bash
just run-observability
```

Этот сценарий reuse-ит существующие `observability/` assets и дополнительно поднимает:

- `loki`
- `alloy`
- `grafana`
- `nginx`

По умолчанию Grafana после этого доступна на `http://127.0.0.1:8088/grafana/`. Успешный сценарий означает, что Grafana открывается, datasource Loki уже provisioned, а логи `bot`, `taskiq-worker` и `taskiq-scheduler` видны в Loki/Grafana.

Прямой Python-запуск остаётся доступным как advanced bot-only path:

```bash
uv run run.py
```

Этот путь не поднимает `worker`, `scheduler`, `redis` и `postgres`.

## Запуск через Docker Compose

Локальный compose поднимает:

- `bot`
- `taskiq-worker`
- `taskiq-scheduler`
- `redis`
- `postgres`
- `health` (optional, `health` profile)
- `loki`, `alloy`, `grafana`, `nginx` (optional, `observability` profile)

Официальная dev/prod-like parity команда:

```bash
just run-parity
```

`just run-parity` вызывает локальный Compose path с `HEALTH_API_ENABLED=true` и `--profile health`, то есть поднимает runtime плюс отдельный `health` process type без ручной сборки флагов. Это основной рекомендуемый путь, если вам нужна максимально близкая к production локальная проверка. `just run-health` остаётся backward-compatible alias.

Базовая local runtime команда:

```bash
just run
```

`just run` вызывает `docker compose up --build`. Эта команда поднимает только базовые runtime-сервисы по умолчанию. `migrate` и `seed` в неё не входят и должны запускаться отдельно как one-shot process types.

Расширенная observability команда:

```bash
just run-observability
```

Она вызывает `docker compose --profile observability up --build` и поднимает runtime вместе с локальным stack'ом `loki/grafana/alloy/nginx`.

Эквивалентная raw compose-команда для dedicated health process type:

```bash
HEALTH_API_ENABLED=true COMPOSE_PROFILES=health docker compose up --build
```

Тот же запуск без `COMPOSE_PROFILES`:

```bash
HEALTH_API_ENABLED=true docker compose --profile health up --build
```

Явный local flow для admin one-shot процессов:

```bash
docker compose --profile migration run --rm migrate
docker compose --profile seed run --rm seed
docker compose --profile backup run --rm backup
```

Особенности локального compose:

- `migrate` запускается отдельным one-shot сервисом и только явно через `docker compose --profile migration run --rm migrate`;
- `seed` запускается отдельным one-shot сервисом и только явно через `docker compose --profile seed run --rm seed`;
- `backup` создаёт PostgreSQL custom-format dump в отдельном backup volume; destructive `restore` запускается только вручную по runbook из `DEPLOYMENT.md`;
- PostgreSQL data volume и backup volume разделены: дампы не хранятся внутри каталога кластера;
- application-сервисы ждут healthy PostgreSQL и Redis перед стартом;
- по умолчанию в compose уже прокинуты `DATABASE_URL`, `TELEGRAM_PROXY_URL`, `FSM_STORAGE_BACKEND=redis`, `REDIS_URL` и `LOG_FORMAT=json`;
- direct local smoke-check для health profile идёт напрямую в health-порт, а не через production ingress path:
  - `GET http://127.0.0.1:8001/` -> `200`
  - `GET http://127.0.0.1:8001/ready` -> `200`, когда приложение готово;
- один официальный dev/prod-like parity path для нового разработчика — `just run-parity`; `just run` остаётся более лёгким локальным runtime, а `uv run run.py` — ручным advanced/debug path;
- local observability profile рассчитан на HTTP-only локальный ingress и не требует production host nginx или public HTTPS path;
- для локального observability profile достаточно встроенных local defaults; вручную собирать отдельный `.env` только ради Grafana/nginx не нужно.

## Проверка качества

Основной обязательный локальный gate:

```bash
just quality-gate
```

Также доступны:

```bash
just test-unit
just test-integration
just docs-build
just test-coverage
```

`just test-unit` не требует Docker. `just test-integration` использует один PostgreSQL 18 Testcontainers container на тестовую сессию, применяет реальную Alembic schema и очищает данные между тестами. В CI второй контейнер не создаётся: тесты получают PostgreSQL service URL через `PYBOT_TEST_DATABASE_URL`. Эта переменная принимает только `postgresql+asyncpg` URL к БД с именем `test` или суффиксом `_test`.

Если локальный `uv` или host Python path не может дать стабильный `Python 3.14`, используйте Docker-based tooling path:

```bash
just test-unit-docker
just test-integration-docker
just test-coverage-docker
just quality-gate-docker
```

Этот путь не заменяет runtime/admin-process model приложения. Он добавляет отдельные one-shot tooling runners через `docker compose --profile tooling run --rm --build ...` и нужен прежде всего для Linux/host Python drift.

Как различать пути:

- host quality/test path:
  - `just test-unit`
  - `just test-integration`
  - `just test-coverage`
  - `just quality-gate`
- docker quality/test path:
  - `just test-unit-docker`
  - `just test-integration-docker`
  - `just test-coverage-docker`
  - `just quality-gate-docker`

Когда использовать:

- host path - когда локальный `Python 3.14` и `uv` уже работают стабильно;
- docker path - когда локальная ОС или пакетный менеджер ломает host tooling path, но Docker Compose доступен.

`test-integration-docker`, `test-coverage-docker` и `quality-gate-docker` используют существующий Compose PostgreSQL, передают `PYBOT_TEST_DATABASE_URL` и автоматически создают `${POSTGRES_DB}_test`, если эта test DB ещё не существует.

## Документация

Перед значимыми изменениями по проекту сначала читайте:

1. `README.md`
2. `ARCHITECTURE.md`
3. `CONTRIBUTING.md`
4. `DEPLOYMENT.md`
5. `SECURITY.md`
6. релевантные ADR из `src/pybot/docs/adr/`

Локальный запуск MkDocs:

```bash
uv sync --extra docs
just docs-serve
```

## Production и деплой

В репозитории уже есть production deployment skeleton:

- `docker-compose.prod.yml` - image-based runtime;
- `.github/workflows/deploy.yml` - CD flow;
- `ansible/` - bootstrap/deploy playbooks.

Production compose использует отдельные one-shot сервисы:

- `migrate` - для `alembic upgrade head`;
- `seed` - для управляемого initial seed;
- `backup` - для custom-format дампа PostgreSQL;
- `restore` - для ручного подтверждаемого восстановления.

Runtime process types в production те же, что и локально: `bot`, `taskiq-worker`, `taskiq-scheduler`, optional `health`, `redis`, `postgres`.

Default image startup is runtime-only: the container entrypoint now runs only `python run.py`. Migrations and seed are never executed implicitly during image startup and remain explicit one-shot operator actions.

Кто и когда запускает one-shot процессы:

- `migrate` на каждом production deploy запускает Ansible до `docker compose up -d`;
- `backup` автоматически запускается Ansible перед production migration;
- `seed` в production запускает Ansible только при `RUN_SEED_ON_DEPLOY=true`;
- `restore` остаётся только ручной destructive-операцией с явным подтверждением;
- в local compose admin one-shot процессы явно запускает сам разработчик или оператор.

Подробнее:

- `DEPLOYMENT.md`
- `ansible/playbooks/deploy.yml`
- `docker-compose.prod.yml`

## Полезные команды

```bash
just
just quality-gate
just docs-build
just migrate-apply
just migrate-create "add new field"
uv run pytest -q
uv run python fill_point_db.py --help
```

## Лицензия

Проект распространяется под лицензией `LICENSE` в корне репозитория.
