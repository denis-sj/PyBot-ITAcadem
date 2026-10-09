# Deployment

This repository uses PostgreSQL 18 for local, test, and production database workloads. Production deployment is image-based and keeps schema migration, seed, backup, and restore as explicit process types.

## What was added

- `docker-compose.prod.yml` for immutable image-based production deploys
- `.github/workflows/deploy.yml` for build + push + deploy after successful CI on `main`, plus manual recovery redeploys and image rollbacks from GitHub Actions
- `ansible/` with a minimal bootstrap/deploy playbook and roles

The CI workflow validates the Docker build, both Compose manifests, the PostgreSQL Alembic upgrade/check/downgrade/upgrade cycle, PostgreSQL-backed integration tests, the local dev/prod parity path (`health` profile + direct probes), and the `fill_point_db.py` CLI help entrypoint before code reaches production deploy.
The deploy workflow additionally fails fast if the checked-out production artifacts (`docker-compose.prod.yml`, `observability/`), the critical deploy secrets, the deploy-only `PROD_ENV_FILE` contract required by Compose, or the runner-side production Compose interpolation contract is violated.

## Database support contract

- PostgreSQL 18 is the only supported runtime and deployment database.
- Application URLs must use `postgresql+asyncpg`.
- Compose application services connect through the `postgres` service hostname.
- `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` must match the percent-decoded database, user, and password components of `DATABASE_URL`.
- SQLite databases and old Alembic revision IDs cannot be upgraded through the current migration chain.
- The current Alembic baseline is intended for a new PostgreSQL database. Data migration from SQLite is outside this deployment flow.

## Deployment flow

1. A push reaches `main`, or an operator manually starts the production workflow from GitHub Actions on `main`.
2. Existing CI runs and must finish successfully for the automatic path.
3. `CD - Build and Deploy` starts either from `workflow_run` or `workflow_dispatch`.
4. GitHub Actions validates `PROD_ENV_FILE` in two runner-side layers: first via `validate_deploy_env.py`, then via `docker compose --env-file ... -f docker-compose.prod.yml config --quiet` with a synthetic `APP_IMAGE` value.
5. GitHub Actions either builds the current `main` image and pushes it to GHCR, or validates that the requested rollback image tag already exists in GHCR.
6. GitHub Actions runs Ansible against the target server.
   The deploy workflow uses `Python 3.14` on the GitHub-hosted runner as the control-node baseline for Ansible.
7. Ansible copies `docker-compose.prod.yml` and `.env`, validates the PostgreSQL contract without printing secrets, pulls images, and runs a one-shot `config-check` process against the selected image and deployed `.env`.
8. Only after `config-check` passes, Ansible starts PostgreSQL 18, creates a custom-format PostgreSQL backup, runs the one-shot `migrate` process only for the standard deploy path, optionally runs `seed`, refreshes the remaining runtime services in place with `docker compose up -d --remove-orphans`, and then explicitly restarts the Compose `nginx` container so bind-mounted observability routing config and current Docker DNS targets are reloaded before smoke checks.
9. Ansible runs a lightweight post-deploy smoke-check for the application processes, PostgreSQL, Redis, and the readiness API when enabled.

## Manual redeploy and rollback

If you need to redeploy the current `main` release without creating an empty commit:

1. Open `Actions` in GitHub.
2. Choose `CD - Build and Deploy`.
3. Click `Run workflow`.
4. Leave `rollback_image_tag` empty.
5. Run it from the `main` branch.

This is useful for recovery, token rotation, or controlled re-runs after infrastructure-side fixes.

If you need to roll back to a previously published immutable image:

1. Open `Actions` in GitHub.
2. Choose `CD - Build and Deploy`.
3. Click `Run workflow`.
4. Run it from the `main` branch.
5. Enter `rollback_image_tag` as the full 40-character SHA tag of the previously known-good image, for example `8b0edd51234abcdeffedcba9876543210abc1234`.
6. Start the workflow.

Rollback mode uses the current `main` deployment automation (`docker-compose.prod.yml`, Ansible, observability assets) but deploys the existing image `ghcr.io/<owner>/<repo>:<rollback_image_tag>` instead of rebuilding a new image from the checked-out source.

The workflow fails fast before SSH/Ansible if the requested rollback image tag does not already exist in GHCR.

Important: image rollback does not roll back database migrations. The workflow intentionally skips the `migrate` step during rollback. Use rollback only when the selected older code is compatible with the current database schema. If schema rollback is required, follow the manual PostgreSQL restore and recovery procedure documented below instead of relying on image rollback alone.

The standard deploy path is in-place and does not run a blanket `docker compose down` first. Compose refreshes only the services whose image, config, or profile inputs actually changed, which reduces avoidable restarts and shortens rollout time on shared hosts.

## Rollback rehearsal

Rehearse rollback on a safe non-production target before relying on it operationally:

1. Publish at least two deployable images so you have a newer image and an older known-good SHA tag.
2. Confirm that the older image is schema-compatible with the current database state.
3. Trigger `CD - Build and Deploy` manually from `main`.
4. Set `rollback_image_tag` to the older known-good full SHA tag.
5. Confirm in the workflow logs that:
   - the rollback warning is shown;
   - the GHCR image existence check passes;
   - the build-and-push step is skipped;
   - Ansible persists `APP_IMAGE` with the requested SHA tag;
   - the existing post-deploy smoke-check reaches success.
6. Run the operator release checks from the runbook below and confirm the target environment is healthy.

## Why production uses a separate compose file

`docker-compose.yml` remains the local/dev entrypoint and still builds from source.

`docker-compose.prod.yml` is intentionally image-based:

- the server does not need the git repository checked out;
- the deployment uses an immutable image tag;
- the deploy host only needs Docker, Compose, `.env`, and persistent volumes.
- both local and production Compose files keep the same process model while differing only in build source (`build` vs `image`).
- the default image startup runs only `python run.py`, so migrations and seed never happen implicitly during container boot.

## Shared Compose process model

Both `docker-compose.yml` and `docker-compose.prod.yml` describe the same operational shape:

- default runtime process types: `bot`, `taskiq-worker`, `taskiq-scheduler`, `redis`
- default database process type: `postgres` using `postgres:18-alpine`
- optional runtime process type: `health` behind the `health` profile
- optional admin one-shot process type: `config-check` behind the `validation` profile
- admin one-shot process type: `migrate` behind the `migration` profile
- admin one-shot process type: `seed` behind the `seed` profile
- admin one-shot process type: `backup` behind the `backup` profile
- admin one-shot process type: `restore` behind the `restore` profile

That alignment is deliberate:

- Factor X Dev/Prod parity: local and production use the same process model
- Factor V Build/Release/Run: runtime processes stay separate from admin one-shot steps
- Factor VI Processes: each process type has an explicit entrypoint instead of hidden startup side effects

Worker concurrency follows the same env-driven mechanism in dev and prod: `taskiq-worker` reads `${TASKIQ_WORKERS:-1}` from Compose. This variable is intentionally outside `AppSettings`; deploy automation and Compose own its contract, and the current supported value remains only `TASKIQ_WORKERS=1`.

For local dev/prod parity checks, the one official recommended path is `just run-parity`. It uses the same Compose-based process model plus the dedicated `health` process type, and the direct local readiness probes stay on `http://127.0.0.1:8001/` and `http://127.0.0.1:8001/ready`. Production ingress checks through Nginx remain a separate outer-layer verification path. The old `just run-health` name remains a backward-compatible alias.

Before the first local parity run, start PostgreSQL and Redis and apply migrations explicitly:

```bash
docker compose up -d --wait postgres redis
docker compose --profile migration run --rm migrate
just run-parity
```

Plain `just run`, `just run-parity`, and `docker compose up` do not apply Alembic migrations automatically.

## Required GitHub secrets

Configure these in the `production` environment or repository secrets:

- `DEPLOY_HOST` - server IP or DNS name
- `DEPLOY_USER` - SSH user used by Ansible
- `DEPLOY_SSH_KEY` - private SSH key for the deploy user
- `DEPLOY_KNOWN_HOSTS` - pinned `known_hosts` entry for the server
- `PROD_ENV_FILE` - full multiline production `.env` file contents

Optional secrets:

- `DEPLOY_PORT` - SSH port, defaults to `22`
- `DEPLOY_PATH` - application directory on the server, defaults to `/home/ilya/pybot`
- `GHCR_DEPLOY_USERNAME` - username for pulling private GHCR images on the server
- `GHCR_DEPLOY_TOKEN` - token for pulling private GHCR images on the server
- `RUN_SEED_ON_DEPLOY` - set to `true` only for the initial deploy when you need to run `fill_point_db.py`

If you use `GHCR_DEPLOY_USERNAME` or `GHCR_DEPLOY_TOKEN`, provide both together. The deploy workflow validates that pair explicitly before running Ansible.

`PROD_ENV_FILE` is treated as a deploy artifact, not as an input to `AppSettings`. Runtime env used by the Python application and deploy/orchestration env used by Compose may live in the same `.env` file operationally, but only the runtime subset is materialized by `get_settings()`.

## Expected server shape

The regular CD deploy assumes:

- Docker is already installed on the server
- the deploy user can run Docker commands without `sudo`
- the deploy path is writable by the deploy user

An optional bootstrap playbook is still available for Debian/Ubuntu hosts:

- [`ansible/playbooks/bootstrap.yml`](/e:/StudBot/PyBot_ITAcadem/ansible/playbooks/bootstrap.yml#L1)
- it installs `docker.io` and `docker-compose-plugin`
- it is intentionally separate from the normal CD path so routine deploys do not mutate shared server infrastructure

The app is deployed into `/home/ilya/pybot` by default and keeps PostgreSQL data, PostgreSQL dumps, and Redis data in separate named Docker volumes:

- `pybot_postgres_data_prod`
- `pybot_postgres_backups_prod`
- `pybot_redis_data_prod`

The deploy also persists the selected released image reference as `APP_IMAGE` inside the server-side `.env`, so routine manual commands like `docker compose ps` and `docker compose logs` work without extra exports.

PostgreSQL 18 mounts `pybot_postgres_data_prod` at `/var/lib/postgresql`. The official image stores the cluster in a version-specific directory below that mount, so this layout follows the PostgreSQL 18 image contract. Dumps are never written into the data volume: `backup` and `restore` share only `/backups` through `pybot_postgres_backups_prod`.

## Notes about ports

`docker-compose.prod.yml` does publish host ports only for the reverse-proxy entrypoint.

That means:

- Redis stays internal to the Docker network
- PostgreSQL stays internal to the Docker network
- the bot does not reserve any host port
- the health API process itself stays internal and is exposed only through the reverse proxy when the `health` profile is enabled
- the current production Compose entrypoint is the `nginx` service, which binds `${NGINX_BIND_HOST:-127.0.0.1}:${NGINX_PORT:-8088}:80`
- public `80/443` are owned by the host Nginx on the shared server

Production services also use strict Docker log rotation limits to reduce disk growth on shared servers.

## HTTPS Observability Endpoint

Production observability is served through the shared host Nginx on:

- `https://monitoring.probochka-corp.ru/grafana/`
- `https://monitoring.probochka-corp.ru/health/`

The expected DNS record is:

```text
monitoring.probochka-corp.ru A 31.163.204.186
```

The runtime path is:

```text
Internet -> host nginx :443 -> http://127.0.0.1:8088 -> compose nginx -> grafana/health
```

The TLS certificate is managed by Certbot on the host, not by the application container. Certificate files must stay on the server under `/etc/letsencrypt` and must never be copied into the repository, logs, fixtures, Docker images, or GitHub Secrets.

The Compose Nginx is an internal HTTP upstream. It must not publish public `80/443` on a shared host.

The deployed production `.env` should include:

```env
PUBLIC_DOMAIN=monitoring.probochka-corp.ru
NGINX_BIND_HOST=127.0.0.1
NGINX_PORT=8088
HEALTH_API_ENABLED=true
```

Create the Certbot webroot on the server:

```bash
sudo mkdir -p /var/www/certbot
```

Install the host Nginx site for `monitoring.probochka-corp.ru`. If the certificate does not exist yet, enable only the `listen 80` server block first, run `sudo nginx -t`, and reload host Nginx.

```nginx
server {
    listen 80;
    server_name monitoring.probochka-corp.ru;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name monitoring.probochka-corp.ru;

    ssl_certificate /etc/letsencrypt/live/monitoring.probochka-corp.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitoring.probochka-corp.ru/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Issue or refresh the certificate through the host Nginx webroot. Do not use `--standalone` on this shared host because host Nginx already owns port `80`.

```bash
sudo certbot certonly --webroot \
  -w /var/www/certbot \
  -d monitoring.probochka-corp.ru \
  --cert-name monitoring.probochka-corp.ru
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
```

After the certificate exists, enable the `listen 443 ssl` server block, then validate and reload host Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Install a Certbot deploy hook so renewed certificates are picked up by host Nginx:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-pybot-nginx.sh >/dev/null <<'SH'
#!/bin/sh
set -eu
systemctl reload nginx
SH
sudo chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-pybot-nginx.sh
```

Post-deploy smoke checks:

```bash
sudo nginx -t
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml exec nginx nginx -t
curl -I http://127.0.0.1:8088/health/
curl -i http://127.0.0.1:8088/health/ready
curl -I http://127.0.0.1:8088/grafana/
curl -I http://monitoring.probochka-corp.ru/health/
curl -I https://monitoring.probochka-corp.ru/health/
curl -I https://monitoring.probochka-corp.ru/grafana/
```

Expected results: internal `127.0.0.1:8088` health returns `200` when `HEALTH_API_ENABLED=true`, internal `/health/ready` returns `200` once the service is ready, internal Grafana returns a successful response or redirect, public HTTP redirects to HTTPS, public `/health/` returns `200`, and public Grafana returns a successful response or redirect under `/grafana/`.

To inspect the certificate served by host Nginx:

```bash
echo | openssl s_client \
  -connect monitoring.probochka-corp.ru:443 \
  -servername monitoring.probochka-corp.ru 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

The SAN must contain `DNS:monitoring.probochka-corp.ru`.

## Loki log retention

Production Loki keeps logs for 14 days.

- `limits_config.retention_period: 336h`
- `limits_config.max_query_lookback: 336h`
- `compactor.retention_enabled: true`
- `compactor.retention_delete_worker_count: 10`

Retention is age-based, not a hard disk quota. A log storm can still fill the disk before 14 days pass, so this change limits history growth but does not replace disk monitoring.

Validate the exact pinned Loki config before rollout:

```bash
docker run --rm \
  -v "$PWD/observability/loki/local-config.yaml:/etc/loki/config.yaml:ro" \
  grafana/loki:3.5.0 \
  -config.file=/etc/loki/config.yaml \
  -verify-config=true
```

Capture the baseline state before changing production Loki:

```bash
docker system df -v
docker volume inspect pybot_loki_data_prod
docker run --rm -v pybot_loki_data_prod:/loki-data alpine sh -c 'du -sh /loki-data'
```

Also record the oldest log timestamp still visible in Grafana Explore so you can confirm the post-rollout retention window later.

Existing Loki data is an explicit rollout choice:

- if old logs are not needed, recreate `pybot_loki_data_prod` once before enabling retention;
- if old logs must be preserved, keep the volume and accept that previously ingested data may remain outside the new retention behavior;
- do not manually delete individual Loki chunk or index files.

One-time Loki volume reset procedure:

```bash
cd /home/ilya/pybot
docker compose -f docker-compose.prod.yml stop alloy grafana nginx loki
docker compose -f docker-compose.prod.yml rm -f loki
docker volume rm pybot_loki_data_prod
docker compose -f docker-compose.prod.yml up -d loki alloy grafana nginx
```

Controlled rollout procedure:

1. Capture baseline Docker disk usage, Loki volume size, and oldest visible log timestamp.
2. Validate `observability/loki/local-config.yaml` locally with `-verify-config=true`.
3. Deploy the config change.
4. Restart only the observability contour: `loki`, `alloy`, `grafana`, `nginx`.
5. Verify Loki readiness, Alloy ingestion, and Grafana queries.
6. Re-check Docker disk usage and Loki volume size after 24 hours.
7. Re-check again after 15-17 days and confirm logs older than 14 days are no longer queryable.

Local smoke path before production rollout:

```bash
just run-observability
docker compose --profile observability ps
docker compose --profile observability logs --tail=100 loki alloy grafana nginx
curl -I http://127.0.0.1:8088/grafana/
```

Treat retention as successfully introduced only when Loki starts cleanly after restart, Alloy still sends logs, Grafana queries continue to work, compactor logs stay free of persistent errors, and old logs disappear after one full retention window. Retention deletions are irreversible once compactor executes them.

## Safety for Shared Servers

The normal CD flow is intentionally non-root:

- it does not install packages during routine deploys
- it does not change system services during routine deploys
- it only writes inside the configured deploy path and uses Docker commands available to the deploy user

This separation is meant to reduce the risk of affecting unrelated projects hosted on the same server.

## Post-Deploy Smoke Check

The deploy role performs a lightweight smoke-check after `docker compose up -d`, grouped as a dedicated post-deploy block in the deploy role:

- verifies that the core process types (`bot`, `taskiq-worker`, `taskiq-scheduler`, `redis`, `postgres`) appear in `docker compose ps`;
- waits for PostgreSQL health to become `healthy`;
- waits for Redis health to become `healthy` when a healthcheck exists;
- captures a baseline runtime snapshot for `bot`, `taskiq-worker`, and `taskiq-scheduler`;
- enforces a 20-second stability window for `bot`, `taskiq-worker`, and `taskiq-scheduler`;
- fails if any of those runtime services stop running, change container ID, or increase `RestartCount` during that observation window;
- when `HEALTH_API_ENABLED=true`, asserts that the `health` service appears in `docker compose ps`;
- when `HEALTH_API_ENABLED=true`, calls `GET http://127.0.0.1:8088/health/ready` through the production Compose Nginx path and fails the deploy unless it reaches `200`.

This complements CI by validating the deployed runtime on the real server instead of rerunning the same test suite, and it adds a real readiness gate for the production health profile.
The same smoke gate is used for both the standard deploy path and rollback deploys; rollback does not add extra probes or change the readiness criteria.

## Operator Release Runbook

Use this short runbook after a manual deploy, failed CD run, or AI-assisted problem-solving session. Run it from the deploy path on the server:

```bash
cd /home/ilya/pybot
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=80 postgres redis bot taskiq-worker taskiq-scheduler health nginx grafana
docker compose -f docker-compose.prod.yml exec nginx nginx -t
curl -I http://127.0.0.1:8088/health/
curl -i http://127.0.0.1:8088/health/ready
curl -I http://127.0.0.1:8088/grafana/
```

A release is healthy when the core services are `Up`, `bot`, `taskiq-worker`, and `taskiq-scheduler` stay `running` without restart-count growth during the smoke window, PostgreSQL and Redis are `healthy`, `nginx -t` succeeds, `/health/` returns `200`, `/health/ready` returns `200`, and `/grafana/` returns a successful Grafana response or redirect.

For the public observability endpoint, verify the host Nginx layer separately:

```bash
sudo nginx -t
curl -Iv --connect-timeout 5 --max-time 15 --resolve monitoring.probochka-corp.ru:4443:127.0.0.1 https://monitoring.probochka-corp.ru:4443/health/
curl -Iv --connect-timeout 5 --max-time 15 --resolve monitoring.probochka-corp.ru:4443:127.0.0.1 https://monitoring.probochka-corp.ru:4443/grafana/
```

Treat the public endpoint as healthy when the certificate is issued for `monitoring.probochka-corp.ru`, `/health/` returns `200`, and `/grafana/` returns a Grafana response or redirect. A timeout from the server to `https://monitoring.probochka-corp.ru/...` can be a hairpin routing limitation; prefer the `--resolve ...:4443:127.0.0.1` checks from the host and an external browser or monitoring check for the real public path.

If a smoke-check fails, do not restart everything blindly. First identify the failing layer:

- Compose service missing or restarting: inspect `docker compose -f docker-compose.prod.yml logs --tail=200 <service>` and fix that service before checking Nginx.
- Internal `127.0.0.1:8088` health or Grafana fails: check `pybot-nginx`, `health`, and `grafana` containers and their logs.
- Internal checks pass but public checks fail: inspect host Nginx with `sudo nginx -T`, `sudo nginx -t`, and `/var/log/nginx/error.log`.
- Certificate warning: inspect the served certificate and confirm the SAN contains `DNS:monitoring.probochka-corp.ru`; then check browser cache only after server-side TLS is confirmed.
- CD smoke-check fails after a new image: prefer redeploying the previous known-good image tag or rerunning the last successful workflow after the root cause is fixed.

Use a full `docker compose -f docker-compose.prod.yml down --remove-orphans` only as a manual recovery action when the normal in-place rollout cannot reconcile the runtime state.

## Health profile

The `health` process type is profile-gated in both Compose files.

- local/manual Compose can enable it with `COMPOSE_PROFILES=health` or `docker compose --profile health ...`
- production deploy enables `--profile health` only when `HEALTH_API_ENABLED=true` is present in the deployed `.env`
- when the profile is disabled, plain `docker compose up -d` does not start `pybot-health`
- when the profile is enabled, the process entrypoint is `uvicorn src.pybot.presentation.web:app`

## Configuration validation

Production deploy validates configuration in three separate layers, each with a narrower and more explicit responsibility:

- runner-side `validate_deploy_env.py` checks that `PROD_ENV_FILE` contains the required deploy/orchestration keys and that the PostgreSQL contract stays internally consistent;
- runner-side `docker compose --env-file "$RUNNER_TEMP/prod.env.validation" -f docker-compose.prod.yml config --quiet` performs canonical Compose interpolation and structure validation with a synthetic `APP_IMAGE`, while a transient repo-root `.env` is materialized only for the duration of that check so `env_file: .env` resolves the same way production Compose expects;
- target-host `config-check` validates runtime `AppSettings` materialization from the selected image and deployed `.env` before PostgreSQL startup, backup, migrations, and runtime refresh.

- local/manual Compose runs `config-check` explicitly with `docker compose --profile validation run --rm --no-deps config-check`;
- production deploy runs `config-check` explicitly via Ansible after image pull and before PostgreSQL startup, backup, migrations, and runtime refresh;
- rollback deploys also run it against the selected older image before the rest of the deploy flow continues;
- it materializes `get_settings()`, asserts `BOT_MODE=prod`, touches `active_bot_token`, and exits without starting PostgreSQL or Redis because the command is intentionally run with `--no-deps`.

The runner-side Compose check does not create containers or start processes; it exists to prove `${VAR}`, `${VAR:-default}`, and `${VAR:?error}` interpolation plus the overall `docker-compose.prod.yml` structure. The later target-host `config-check` validates runtime env parsing and `Pydantic` validators only. Neither of these gates proves database reachability, Redis reachability, DI wiring, or application readiness, so they complement rather than replace the later post-deploy smoke and readiness gates.

## Migrations

Migrations are executed by the dedicated `migrate` service, not by `bot` startup.

That service is attached to the `migration` profile, so:

- local/manual Compose runs it explicitly with `docker compose --profile migration run --rm migrate`;
- production deploy runs it explicitly via Ansible before `docker compose up -d` on the standard deploy path;
- rollback deploys intentionally skip this step because image rollback does not imply schema rollback;
- a plain `docker compose up -d` or `docker compose up --build` does not start it.

The active migration history begins with the PostgreSQL initial baseline. It is not connected to the removed SQLite migration history. Apply it only to a new PostgreSQL database or to a database already managed by this baseline.

### Attendance Photo Accounting Rollout & Migration Safety

The attendance photo accounting feature introduces revision `8d6ac9f0b6a0_add_attendance_tables`.

#### Pre-Migration Backup (Mandatory)
Before applying the migration to a production database, create a dedicated custom-format backup of the database. In production, PostgreSQL cluster data resides in the `pybot_postgres_data_prod` volume, and dumps are stored in `pybot_postgres_backups_prod`.

Production backup:
```bash
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U pybot -Fc pybot_itacadem > backup_pre_attendance.dump
```
Alternatively, trigger the automated backup service in production:
```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm backup
```

Local development backup (`docker-compose.yml`):
```bash
docker compose exec -T postgres pg_dump -U pybot -Fc pybot_itacadem > backup_pre_attendance.dump
# or
docker compose --profile backup run --rm backup
```

#### Running the Migration
Apply the migration using the standard deploy process or one-shot migration runner:
```bash
# Production:
docker compose -f docker-compose.prod.yml --profile migration run --rm migrate

# Local development:
docker compose --profile migration run --rm migrate

# Or directly with Alembic:
alembic upgrade head
```

#### Verification of Data Preservation
The migration is **strictly additive**:
- Creates `attendance_imports`, `attendance_dates`, and `attendance_records` tables.
- Creates unique constraints and indexes for attendance tracking.
- Does **not** drop, truncate, or alter any existing tables (`users`, `roles`, `levels`, `competencies`, `points_transactions`, `valuations`, etc.).
- All existing rows and foreign key relationships remain completely intact.

Verify data integrity after migration:
```bash
# Production:
docker compose -f docker-compose.prod.yml exec postgres psql -U pybot -d pybot_itacadem -c "SELECT COUNT(*) FROM users;"
docker compose -f docker-compose.prod.yml exec postgres psql -U pybot -d pybot_itacadem -c "\d attendance_records"

# Local development:
docker compose exec postgres psql -U pybot -d pybot_itacadem -c "SELECT COUNT(*) FROM users;"
docker compose exec postgres psql -U pybot -d pybot_itacadem -c "\d attendance_records"
```

#### Rollback Procedure

**1. Application Rollback (Non-destructive, Recommended):**
If only application code needs to be rolled back, use image rollback (`rollback_image_tag`). The older application image works with the existing schema and does not touch or delete any attendance data. All stored records, dates, and photo evidence are fully preserved.

**2. Schema Rollback (DESTRUCTIVE — Extreme Caution):**
> ⚠️ **CRITICAL WARNING:** `alembic downgrade 1de8107fe120` is a **DESTRUCTIVE schema rollback**!
> It drops the `attendance_records`, `attendance_dates`, and `attendance_imports` tables, **permanently erasing all stored attendance records and original photo evidence**!
> Do NOT run a schema downgrade unless you intentionally want to delete all attendance data.

If schema downgrade is explicitly required:
```bash
# Production:
docker compose -f docker-compose.prod.yml --profile migration run --rm migrate alembic downgrade 1de8107fe120

# Local development:
docker compose --profile migration run --rm migrate alembic downgrade 1de8107fe120
```
- The downgrade drops **only** the attendance tables (`attendance_records`, `attendance_dates`, `attendance_imports`) and their indexes from the database (in production, stored on `pybot_postgres_data_prod`).
- It does **not** touch user, points, or role data.
- If a complete database restore is needed, use the pre-migration dump:
```bash
# Production:
RESTORE_FILE=backup_pre_attendance.dump CONFIRM_RESTORE=YES docker compose -f docker-compose.prod.yml --profile restore run --rm restore

# Local development:
RESTORE_FILE=backup_pre_attendance.dump CONFIRM_RESTORE=YES docker compose --profile restore run --rm restore
```
#### Environment Configuration
Add the following optional variables to `.env` if Polza.ai vision recognition is enabled:
- `POLZA_API_KEY`: API key for Polza.ai (left blank if recognition is disabled).
- `POLZA_BASE_URL`: API base URL (default: `https://polza.ai/api/v1`).
- `POLZA_VISION_MODEL`: Model name (default: `google/gemini-2.5-flash`).
- `POLZA_TIMEOUT_S`: Request timeout in seconds (default: `60`, min 5, max 300).
- `ATTENDANCE_TIMEZONE`: Timezone for attendance calculation and monthly reports (default: `Asia/Yekaterinburg`).

## Seed

Seed data is handled by the dedicated `seed` service, not by `bot` startup.

- it is disabled by default;
- local/manual Compose runs it explicitly with `docker compose --profile seed run --rm seed`;
- production deploy runs it only on the standard deploy path when `RUN_SEED_ON_DEPLOY=true` is passed from GitHub Secrets into the deploy workflow;
- rollback deploys intentionally skip seed even if the secret is still enabled, so old images do not replay initialization logic accidentally;
- it is intended for first deploys or controlled reinitialization, not for every rollout.

The seed process is not fully atomic. Its levels and roles steps, along with
application services used for competencies and fake users, perform intermediate
commits. If a later required step fails, the CLI rolls back the current
uncommitted transaction and exits with a non-zero status so Compose and Ansible
stop the deploy step. That rollback does not undo changes committed by earlier
steps, so operators must treat a failed run as potentially partially applied
and inspect the database before retrying.

## PostgreSQL backup and restore

The `backup` service uses `postgres:18-alpine` and creates a custom-format dump with `pg_dump --format=custom --no-owner --no-privileges`. Every production deploy runs it automatically after PostgreSQL becomes healthy and before the remaining deploy steps. Standard deploys then run `migrate`; rollback deploys keep the backup but skip migrations. Dumps are written with restricted permissions to the separate backup volume and named `pybot-<UTC timestamp>.dump`.

Run an additional manual backup from the deploy directory with:

```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm backup
```

List available dump basenames without mounting the backup volume on the host:

```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm \
  --entrypoint sh backup -c 'ls -lh /backups'
```

There is no automatic retention policy. Operators must monitor the backup volume and delete obsolete dumps manually according to the project's operational requirements.

Restore is intentionally destructive and manual. The `restore` service accepts only a dump basename through `RESTORE_FILE`, rejects paths and missing files, and requires exact `CONFIRM_RESTORE=YES`. These values must be passed for a single command and must not be stored in `.env`.

Restore runbook:

1. Stop all writing application services.
2. Select a dump basename from the backup volume.
3. Run the confirmed restore. It terminates other connections to the target database and executes `pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error`.
4. Apply the current Alembic migration head.
5. Start the runtime again.
6. Verify PostgreSQL health and `GET /health/ready`.

```bash
docker compose -f docker-compose.prod.yml stop bot taskiq-worker taskiq-scheduler health

RESTORE_FILE=pybot-20260609T120000Z.dump CONFIRM_RESTORE=YES \
  docker compose -f docker-compose.prod.yml --profile restore run --rm restore

docker compose -f docker-compose.prod.yml --profile migration run --rm migrate
docker compose -f docker-compose.prod.yml --profile health up -d --remove-orphans
docker compose -f docker-compose.prod.yml ps postgres
curl -i http://127.0.0.1:8088/health/ready
```

Omit `--profile health` and the readiness request when `HEALTH_API_ENABLED=false`.

Before relying on a backup operationally, rehearse this flow on a non-production environment: create control data, make a dump, change or remove the data, stop writers, restore the dump, run migrations, and confirm that the control data returned.

## Recommended production `.env` baseline

At minimum, set:

- `BOT_TOKEN`
- `BOT_TOKEN_TEST` - optional when `BOT_MODE=prod`, but keep it if you still use test-mode launches in that environment
- `BOT_MODE=prod`
- `ROLE_REQUEST_ADMIN_TG_ID`
- `POSTGRES_DB=pybot_itacadem`
- `POSTGRES_USER=pybot`
- `POSTGRES_PASSWORD=<raw strong password>`
- `DATABASE_URL=postgresql+asyncpg://pybot:<percent-encoded password>@postgres:5432/pybot_itacadem`
- `FSM_STORAGE_BACKEND=redis`
- `REDIS_URL=redis://redis:6379/0`
- `LOG_LEVEL=INFO`
- `HEALTH_API_ENABLED=true`
- `LEADERBOARD_WEEKLY_RETRY_ENABLED=true`
- `LEADERBOARD_WEEKLY_RETRY_MAX_RETRIES=3`
- `LEADERBOARD_WEEKLY_RETRY_DELAY_S=30`
- `LEADERBOARD_WEEKLY_RETRY_USE_JITTER=true`
- `LEADERBOARD_WEEKLY_RETRY_USE_EXPONENTIAL_BACKOFF=true`
- `LEADERBOARD_WEEKLY_RETRY_MAX_DELAY_S=300`

Deploy / orchestration-only baseline:

- `GRAFANA_ADMIN_PASSWORD`
- `TASKIQ_WORKERS=1`
- `PUBLIC_DOMAIN=monitoring.probochka-corp.ru`
- `NGINX_BIND_HOST=127.0.0.1`
- `NGINX_PORT=8088`

Important:

- production `DATABASE_URL` must use `postgresql+asyncpg`, hostname `postgres`, and database/user/password values matching `POSTGRES_*`;
- keep the raw password in `POSTGRES_PASSWORD`, but percent-encode URL-special characters in the password component of `DATABASE_URL`;
- `POSTGRES_PORT` is only needed for the local host binding; production does not publish port `5432`;
- host-only commands must temporarily use `127.0.0.1` instead of the Compose hostname `postgres`;
- when `HEALTH_API_ENABLED=true`, deploy orchestration enables the `health` Compose profile and starts a dedicated `pybot-health` service (`uvicorn src.pybot.presentation.web:app`)
- production uses the same concurrency knob as local Compose: `TASKIQ_WORKERS=1 docker compose up -d`
- syntax for future worker scaling is already reserved via `TASKIQ_WORKERS`, but values greater than `1` are intentionally rejected for now
- weekly leaderboard retries are applied only for temporary delivery failures (`NotificationTemporaryError`)
- retry policy for weekly publishing is controlled by `LEADERBOARD_WEEKLY_RETRY_*` env settings

## Next hardening steps

- Add external monitoring/log shipping
- Add image vulnerability scanning before deploy

## Agent harness (stage 0, opt-in)

`AGENT_ENABLED=false` is the default: the agent router is not registered and ordinary
bot behavior stays unchanged. To enable the agent in a test bot, set `AGENT_ENABLED=true`
and the existing `POLZA_API_KEY`. The independent `AGENT_MODEL` defaults to
`google/gemini-3.5-flash-lite` (verified in the [Polza catalog](https://polza.ai/models/google/gemini-3.5-flash-lite)).
`POLZA_BASE_URL` is shared with vision; `POLZA_VISION_MODEL` remains unchanged.

Optional agent settings:

- `AGENT_GROUP_CHAT_ID` and `AGENT_TOPIC_ID`: configure together (negative group ID,
  positive topic ID), or leave both blank. That topic accepts unaddressed text;
  other group locations require `@bot` or a reply to an agent message.
- `AGENT_MEMORY_MAX_MESSAGES=10`: between 2 and 10 text messages, including both
  questions and answers. Each message is truncated to 2000 characters.
- `AGENT_MEMORY_TTL_S=1800`: between 1 and 1800 seconds, renewed after a completed
  turn; also the pending approval TTL.
- `AGENT_REPLY_MARK_TTL_S=604800`: positive seconds; agent reply markers expire
  after seven days by default. Outside the configured agent topic, a reply to an
  older message needs `@bot` to address the agent.
- `AGENT_MAX_STEPS=8`: model request limit, between 1 and 20; resumed approvals share
  the original request budget. Each run also permits at most 8 tool calls.
- `AGENT_TIMEOUT_S=60`: between 5 and 300 seconds for a run, excluding time in the
  per-user queue and time waiting for approval. The HTTP client has the same timeout.

Redis is required for agent memory even when `FSM_STORAGE_BACKEND=memory`.
Text memory keys include chat, topic and author. Tool traces are not persisted.
Agent reply provenance is expiring metadata (chat/topic/message IDs only),
independent of text memory. Replying after the memory TTL starts a new question
while the reply marker is still alive. Existing markers written before the TTL
setting was introduced retain their original Redis lifetime; the TTL applies to
new replies.
Confirmation continuations are process-local, bound to their author and exact
keyboard message; restarting the bot invalidates them. The local FIFO queue holds
through Telegram delivery and is cleaned when no waiter remains. This follows the
current single-replica bot contract; multiple replicas require distributed sequencing
and approval consumption before enabling the agent there.

At stage 0 the only agent tool is `bot_capabilities`; it lists commands allowed for
the actor's roles and makes no data changes. Actual reading and writing tools will
be introduced in subsequent stages. Commands, active FSM scenarios, active
`aiogram-dialog` contexts and non-text messages keep their existing handlers.

Tests use Pydantic AI `TestModel`/`FunctionModel` and prohibit network model requests.
Live evaluations are opt-in and billed by Polza: `just eval-agent`, or
`uv run python evals/agent/run.py`. They use synthetic users without database or
Telegram access; they print case status and tool names, not provider bodies or keys.
They are not part of CI. During a test-bot smoke check, verify private questions,
the configured topic, mentions, replies to agent versus ordinary bot messages,
registration/broadcast text entry, and model timeout UX.
