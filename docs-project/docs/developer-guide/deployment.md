# Деплой

Полное описание деплоя находится в корневом `DEPLOYMENT.md`. Эта страница дает быстрый обзор для навигации в сайте документации.

## Базовый контур

1. CI на `main` должен пройти успешно.
2. Workflow `CD - Build and Deploy` сначала валидирует `PROD_ENV_FILE` на GitHub Actions runner через `validate_deploy_env.py`, затем через `docker compose ... config --quiet` проверяет production Compose interpolation contract.
3. После runner-side validation workflow либо собирает и публикует Docker image, либо проверяет существование rollback image в GHCR.
4. Ansible разворачивает `docker-compose.prod.yml` на сервере.
5. До старта PostgreSQL и runtime Ansible запускает отдельный `config-check` process type для materialization `AppSettings` из реального production `.env`.
6. Затем PostgreSQL 18 запускается и проходит healthcheck.
7. Отдельный backup-контейнер создаёт custom-format dump, migration-контейнер применяет миграции, а seed выполняется только на standard deploy path при явном `RUN_SEED_ON_DEPLOY=true`.
8. После этого обновляется runtime и выполняется финальный post-deploy smoke-check с readiness gate.

TaskIQ scheduler при запуске идемпотентно регистрирует расписания функций сообщества в часовом поясе
`Asia/Yekaterinburg`: опрос в среду в 15:00, напоминание в четверг в 11:00, PDF-отчёт в четверг в 15:00
и предупреждение о серии в субботу в 12:00. Для них не нужны дополнительные переменные окружения; индивидуальные
переключатели хранятся в PostgreSQL и доступны в боте через `/notifications`.

Production observability now also has a documented Loki retention path: the root `DEPLOYMENT.md` describes the 14-day retention target, the pinned `grafana/loki:3.5.0 -verify-config=true` validation command, the operator choice about preserving or recreating `pybot_loki_data_prod`, and the controlled rollout/rehearsal procedure.

Workflow `CD - Build and Deploy` now also supports a manual rollback path via the optional `rollback_image_tag` input. The root `DEPLOYMENT.md` is the source of truth for operator steps, GHCR image validation, the schema-compatibility warning, and the separate database restore/recovery procedure to use when image rollback is not sufficient.

## Ключевые файлы

- `docker-compose.prod.yml`
- `.github/workflows/deploy.yml`
- `ansible/playbooks/bootstrap.yml`
- `ansible/playbooks/deploy.yml`

## Что не забывать

- синхронизировать `.env.example` и runtime config;
- использовать только `postgresql+asyncpg` URL, согласованный с `POSTGRES_*`;
- проверять PostgreSQL native enums, `BigInteger` PK/FK и Alembic drift;
- помнить, что текущая PostgreSQL baseline не обновляет старые SQLite базы и revision ID;
- проверять restore на тестовом стенде, а не только факт создания backup;
- документировать rollback и operator steps, если изменение не backward-compatible.
