from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "compose_file",
    ("docker-compose.yml", "docker-compose.prod.yml"),
)
def test_taskiq_worker_command_preserves_container_shell_variable(compose_file: str) -> None:
    project_root = Path(__file__).resolve().parents[2]
    compose_config = (project_root / compose_file).read_text(encoding="utf-8")

    assert "TASKIQ_WORKERS='${TASKIQ_WORKERS:-1}'" in compose_config
    assert '\\"$$TASKIQ_WORKERS\\" != \\"1\\"' in compose_config
    assert '--workers \\"$$TASKIQ_WORKERS\\"' in compose_config


@pytest.mark.parametrize(
    "compose_file",
    ("docker-compose.yml", "docker-compose.prod.yml"),
)
def test_compose_files_define_config_check_validation_process(compose_file: str) -> None:
    project_root = Path(__file__).resolve().parents[2]
    compose_config = (project_root / compose_file).read_text(encoding="utf-8")

    assert "config-check:" in compose_config
    assert "config-check:\n    <<: *app-base" in compose_config
    assert 'profiles: ["validation"]' in compose_config or 'profiles: [ "validation" ]' in compose_config
    assert 'restart: "no"' in compose_config
    assert "from pybot.core.config import get_settings; settings = get_settings();" in compose_config
    assert "assert settings.bot_mode == 'prod', settings.bot_mode;" in compose_config
    assert "settings.active_bot_token;" in compose_config
    assert "Runtime configuration is valid" in compose_config


def test_staging_compose_is_isolated_and_keeps_backup_migration_order_contract() -> None:
    project_root = Path(__file__).resolve().parents[2]
    staging_compose = (project_root / "docker-compose.staging.yml").read_text(encoding="utf-8")

    assert "name: pybot-staging" in staging_compose
    assert "name: pybot_staging_postgres_data" in staging_compose
    assert "name: pybot_staging_postgres_backups" in staging_compose
    assert "name: pybot_staging_redis_data" in staging_compose
    assert 'backup_file="/backups/pybot-staging-$${timestamp}.dump"' in staging_compose
    assert 'command: ["alembic", "upgrade", "head"]' in staging_compose


def test_ci_runs_for_feature_branches_and_validates_staging_compose() -> None:
    project_root = Path(__file__).resolve().parents[2]
    ci_workflow = (project_root / ".github" / "workflows" / "ci.yaml").read_text(encoding="utf-8")

    assert "'feat/**'" in ci_workflow
    assert "docker-compose.staging.yml config > /dev/null" in ci_workflow


def test_staging_workflow_is_fork_scoped_and_uses_only_staging_secrets() -> None:
    project_root = Path(__file__).resolve().parents[2]
    staging_workflow = (project_root / ".github" / "workflows" / "deploy-staging.yml").read_text(encoding="utf-8")

    assert "github.repository == 'denis-sj/PyBot-ITAcadem'" in staging_workflow
    assert "github.event.workflow_run.event == 'push'" in staging_workflow
    assert "github.event.workflow_run.head_branch == 'main'" in staging_workflow
    assert "workflow_dispatch:" in staging_workflow
    assert "environment: staging" in staging_workflow
    assert "group: pybot-staging-deploy" in staging_workflow
    assert "-p pybot-staging" in staging_workflow
    assert "secrets.PROD_" not in staging_workflow
    assert "secrets.STAGING_" in staging_workflow
    assert "--profile backup run --rm backup" in staging_workflow
    assert "--profile migration run --rm migrate" in staging_workflow
    assert staging_workflow.index("--profile backup run --rm backup") < staging_workflow.index(
        "--profile migration run --rm migrate"
    )


def test_ci_parity_override_mounts_existing_bot_smoke_helper_read_only() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parity_override = (project_root / "docker-compose.ci.parity.override.yml").read_text(encoding="utf-8")

    assert (project_root / "scripts/ci/bot_runtime_parity_smoke.py").is_file()
    assert 'command: ["python", "scripts/ci/bot_runtime_parity_smoke.py"]' in parity_override
    assert "./scripts/ci:/app/scripts/ci:ro" in parity_override


def test_ci_parity_smoke_migrates_postgresql_before_runtime() -> None:
    project_root = Path(__file__).resolve().parents[2]
    smoke_script = (project_root / "scripts/ci/run_parity_smoke_check.sh").read_text(encoding="utf-8")

    backing_services_command = "up -d --wait --wait-timeout 120 postgres redis"
    migration_command = "--profile migration run --rm --build migrate"
    runtime_command = "up -d --build \\\n  bot taskiq-worker taskiq-scheduler health"

    assert "required_services=(bot taskiq-worker taskiq-scheduler postgres redis health)" in smoke_script
    assert smoke_script.index(backing_services_command) < smoke_script.index(migration_command)
    assert smoke_script.index(migration_command) < smoke_script.index(runtime_command)


def test_production_seed_pipeline_always_skips_fake_users() -> None:
    project_root = Path(__file__).resolve().parents[2]
    production_compose = (project_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    deploy_tasks = (project_root / "ansible/roles/pybot_deploy/tasks/main.yml").read_text(encoding="utf-8")

    assert 'command: [ "pybot-seed", "--skip-fake-users" ]' in production_compose
    assert "seed pybot-seed --skip-fake-users" in deploy_tasks


def test_production_deploy_workflow_supports_optional_manual_rollback_image_tag() -> None:
    project_root = Path(__file__).resolve().parents[2]
    deploy_workflow = (project_root / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert "rollback_image_tag:" in deploy_workflow
    assert "required: false" in deploy_workflow
    assert "Resolve deploy mode" in deploy_workflow
    assert "Rollback image compatibility warning" in deploy_workflow
    assert "Verify rollback image exists in GHCR" in deploy_workflow
    assert "docker buildx imagetools inspect" in deploy_workflow
    assert "Build and push production image" in deploy_workflow
    assert "if: steps.prep.outputs.rollback_mode != 'true'" in deploy_workflow


def test_production_deploy_workflow_validates_compose_contract_on_runner_before_build_or_rollback() -> None:
    project_root = Path(__file__).resolve().parents[2]
    deploy_workflow = (project_root / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    validation_step = "Validate required deploy secrets and runner-side production config contracts"
    build_step = "Build and push production image"
    rollback_verify_step = "Verify rollback image exists in GHCR"

    assert validation_step in deploy_workflow
    assert 'printf \'%s\\n\' "$PROD_ENV_FILE" > "$validation_env_path"' in deploy_workflow
    assert 'python "$GITHUB_WORKSPACE/scripts/ci/validate_deploy_env.py" "$validation_env_path"' in deploy_workflow
    assert 'cp "$validation_env_path" "$workspace_env_path"' in deploy_workflow
    assert "trap cleanup_validation_files EXIT" in deploy_workflow
    assert 'APP_IMAGE="${validation_image_repo}:validation" \\' in deploy_workflow
    assert '--env-file "$validation_env_path" \\' in deploy_workflow
    assert "-f docker-compose.prod.yml \\" in deploy_workflow
    assert "config --quiet" in deploy_workflow
    assert deploy_workflow.index(validation_step) < deploy_workflow.index(build_step)
    assert deploy_workflow.index(validation_step) < deploy_workflow.index(rollback_verify_step)


def test_ci_workflow_runs_real_ansible_syntax_checks_for_deploy_playbooks() -> None:
    project_root = Path(__file__).resolve().parents[2]
    ci_workflow = (project_root / ".github" / "workflows" / "ci.yaml").read_text(encoding="utf-8")

    assert "Install ansible-core for playbook syntax checks" in ci_workflow
    assert "Run Ansible playbook syntax checks" in ci_workflow
    assert "ansible/playbooks/deploy.yml" in ci_workflow
    assert "ansible/playbooks/bootstrap.yml" in ci_workflow
    assert "--syntax-check" in ci_workflow


def test_production_deploy_role_keeps_image_based_runtime_and_conditional_migrations() -> None:
    project_root = Path(__file__).resolve().parents[2]
    production_compose = (project_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    deploy_tasks = (project_root / "ansible" / "roles" / "pybot_deploy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "${APP_IMAGE:?APP_IMAGE must be set for production deploys}" in production_compose
    assert "rollback_mode is defined" in deploy_tasks
    assert "run_migrations is defined" in deploy_tasks
    assert "when: run_migrations | bool" in deploy_tasks
    assert "run_seed_on_deploy | default(false) | bool" in deploy_tasks
    assert "not rollback_mode | bool" in deploy_tasks


def test_production_deploy_post_deploy_smoke_enforces_runtime_stability_window() -> None:
    project_root = Path(__file__).resolve().parents[2]
    deploy_tasks = (project_root / "ansible" / "roles" / "pybot_deploy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "Capture runtime service stability baseline" in deploy_tasks
    assert "Wait for runtime stability observation window" in deploy_tasks
    assert "Verify runtime services remain stable through observation window" in deploy_tasks
    assert "for service in bot taskiq-worker taskiq-scheduler; do" in deploy_tasks
    assert "docker inspect -f '{{ \"{{.State.Status}}\" }}'" in deploy_tasks
    assert "docker inspect -f '{{ \"{{.RestartCount}}\" }}'" in deploy_tasks
    assert "register: runtime_service_stability_baseline" in deploy_tasks
    assert "seconds: 20" in deploy_tasks
    assert (
        "while IFS=\"$(printf '\\t')\" read -r service baseline_cid baseline_status baseline_restart_count; do"
        in deploy_tasks
    )
    assert 'stdin: "{{ runtime_service_stability_baseline.stdout }}"' in deploy_tasks
    assert "snapshot.split('\\t')" not in deploy_tasks
    assert "service no longer resolves to a container id after the 20-second observation window" in deploy_tasks
    assert "container changed during the 20-second observation window" in deploy_tasks
    assert "current status is $current_status instead of running" in deploy_tasks
    assert "RestartCount increased during the 20-second observation window" in deploy_tasks
    assert 'if [ "$current_cid" != "$baseline_cid" ]; then' in deploy_tasks
    assert 'if [ "$current_restart_count" -ne "$baseline_restart_count" ]; then' in deploy_tasks


def test_production_deploy_runs_config_check_before_starting_postgres_and_migrations() -> None:
    project_root = Path(__file__).resolve().parents[2]
    deploy_tasks = (project_root / "ansible" / "roles" / "pybot_deploy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    config_check_command = (
        "docker compose -f docker-compose.prod.yml --profile validation run --rm --no-deps config-check"
    )
    pull_step = "Pull production images"
    config_step = "Validate runtime configuration before starting deploy dependencies"
    postgres_step = "Start PostgreSQL"
    migrate_step = "Run one-shot database migrations"

    assert "--profile validation" in deploy_tasks
    assert config_check_command in deploy_tasks
    assert deploy_tasks.index(pull_step) < deploy_tasks.index(config_step)
    assert deploy_tasks.index(config_step) < deploy_tasks.index(postgres_step)
    assert deploy_tasks.index(config_step) < deploy_tasks.index(migrate_step)


def test_production_deploy_restarts_nginx_after_compose_apply_before_smoke() -> None:
    project_root = Path(__file__).resolve().parents[2]
    deploy_tasks = (project_root / "ansible" / "roles" / "pybot_deploy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    compose_step = "Apply production compose"
    nginx_restart_step = "Restart compose nginx to reload observability routing config"
    smoke_step = "Run post-deploy smoke checks"

    assert nginx_restart_step in deploy_tasks
    assert "docker compose -f docker-compose.prod.yml restart nginx" in deploy_tasks
    assert deploy_tasks.index(compose_step) < deploy_tasks.index(nginx_restart_step)
    assert deploy_tasks.index(nginx_restart_step) < deploy_tasks.index(smoke_step)
