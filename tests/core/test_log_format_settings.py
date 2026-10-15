from __future__ import annotations

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from pybot.core.config import AppSettings

ADMIN_TG_ID = 123_456_789


class AppSettingsWithoutDotenv(AppSettings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings)


def test_log_format_defaults_to_text_for_test_mode(monkeypatch) -> None:
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        BOT_MODE="test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.log_format == "text"


def test_log_format_defaults_to_json_for_prod_mode(monkeypatch) -> None:
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        BOT_MODE="prod",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.log_format == "json"


def test_explicit_log_format_overrides_prod_runtime_default() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        BOT_MODE="prod",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        LOG_FORMAT="text",
    )

    assert parsed_settings.log_format == "text"


def test_fsm_storage_defaults_to_redis_for_local_runtime() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        BOT_MODE="test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.fsm_storage_backend == "redis"
    assert parsed_settings.redis_url == "redis://localhost:6379/0"
