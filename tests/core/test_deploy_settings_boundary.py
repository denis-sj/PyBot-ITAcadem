import os
from pathlib import Path
import subprocess
import sys

from pybot.core.config import AppSettings

_CONFIG_CHECK_COMMAND = "from pybot.core.config_check import main; main()"


def test_app_settings_do_not_include_deploy_only_fields() -> None:
    for field_name in (
        "taskiq_workers",
        "grafana_admin_password",
        "nginx_port",
        "nginx_bind_host",
        "public_domain",
    ):
        assert field_name not in AppSettings.model_fields


def test_get_settings_succeeds_without_deploy_only_env(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_TOKEN"] = "123456:prod"
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"
    for key in (
        "BOT_TOKEN_TEST",
        "TASKIQ_WORKERS",
        "GRAFANA_ADMIN_PASSWORD",
        "NGINX_PORT",
        "NGINX_BIND_HOST",
        "PUBLIC_DOMAIN",
    ):
        runtime_env.pop(key, None)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from pybot.core.config import get_settings; get_settings(); print('ok')"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode == 0
    assert "ok" in result.stdout


def test_get_settings_ignores_deploy_only_keys_from_dotenv(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["BOT_TOKEN"] = "123456:prod"
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"
    for key in (
        "BOT_TOKEN_TEST",
        "TASKIQ_WORKERS",
        "GRAFANA_ADMIN_PASSWORD",
        "NGINX_PORT",
        "NGINX_BIND_HOST",
        "PUBLIC_DOMAIN",
    ):
        runtime_env.pop(key, None)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TASKIQ_WORKERS=1",
                "GRAFANA_ADMIN_PASSWORD=strong-password",
                "NGINX_PORT=8080",
                "NGINX_BIND_HOST=0.0.0.0",
                "PUBLIC_DOMAIN=example.com",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from pybot.core.config import get_settings; get_settings(); print('ok')"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode == 0
    assert "ok" in result.stdout


def test_config_check_contract_succeeds_with_valid_production_like_runtime_env(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["BOT_TOKEN"] = "123456:prod"
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"
    runtime_env["TELEGRAM_PROXY_URL"] = "socks5h://proxy.example:1080"

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CONFIG_CHECK_COMMAND],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode == 0
    assert "Runtime configuration is valid" in result.stdout


def test_config_check_contract_fails_fast_on_invalid_runtime_env(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"
    runtime_env.pop("BOT_TOKEN", None)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CONFIG_CHECK_COMMAND],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode != 0
    assert "BOT_TOKEN" in result.stderr or "ValidationError" in result.stderr


def test_config_check_contract_fails_fast_on_empty_production_bot_token(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["BOT_TOKEN"] = "   "
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CONFIG_CHECK_COMMAND],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode != 0
    assert "BOT_TOKEN" in result.stderr
    assert "must be set" in result.stderr


def test_config_check_reports_unsupported_telegram_proxy_scheme_without_value(tmp_path: Path) -> None:
    runtime_env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    runtime_env["PYTHONPATH"] = os.pathsep.join(
        [
            str(project_root / "src"),
            str(project_root),
            runtime_env.get("PYTHONPATH", ""),
        ]
    )
    runtime_env["BOT_MODE"] = "prod"
    runtime_env["BOT_TOKEN"] = "123456:prod"
    runtime_env["ROLE_REQUEST_ADMIN_TG_ID"] = "1"
    runtime_env["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test"
    runtime_env["TELEGRAM_PROXY_URL"] = "ftp://proxy-user:secret-password@proxy.example:1080"

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pybot.core.config_check"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
    )

    assert result.returncode != 0
    assert "TELEGRAM_PROXY_URL" in result.stderr
    assert "Supported schemes: http, socks4, socks5, socks5h" in result.stderr
    assert "secret-password" not in result.stderr
