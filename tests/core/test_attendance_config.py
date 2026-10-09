from typing import Any

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
import pytest

from pybot.core.config import AppSettings


class AppSettingsWithoutDotenv(AppSettings):
    """Subclass that disables loading from .env files for deterministic testing."""

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


def test_polza_settings_default_values() -> None:
    settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
    )

    assert settings.polza_api_key is None
    assert settings.polza_base_url == "https://polza.ai/api/v1"
    assert settings.polza_vision_model == "google/gemini-2.5-flash"
    assert settings.polza_timeout_s == 60
    assert str(settings.attendance_timezone) == "Asia/Yekaterinburg"


def test_polza_settings_custom_values() -> None:
    settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        POLZA_API_KEY=SecretStr("test_polza_key_123"),
        POLZA_BASE_URL="https://custom.ai/api/v1",
        POLZA_VISION_MODEL="openai/gpt-4o",
        POLZA_TIMEOUT_S=120,
        ATTENDANCE_TIMEZONE="Europe/Moscow",
    )

    assert isinstance(settings.polza_api_key, SecretStr)
    assert settings.polza_api_key.get_secret_value() == "test_polza_key_123"
    assert "test_polza_key_123" not in repr(settings.polza_api_key)
    assert settings.polza_base_url == "https://custom.ai/api/v1"
    assert settings.polza_vision_model == "openai/gpt-4o"
    assert settings.polza_timeout_s == 120
    assert str(settings.attendance_timezone) == "Europe/Moscow"


def test_polza_timeout_validation_lower_bound() -> None:
    with pytest.raises(ValidationError, match="POLZA_TIMEOUT_S"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            POLZA_TIMEOUT_S=4,
        )


def test_polza_timeout_validation_upper_bound() -> None:
    with pytest.raises(ValidationError, match="POLZA_TIMEOUT_S"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            POLZA_TIMEOUT_S=301,
        )


def test_yekaterinburg_tz_constant() -> None:
    from pybot.core.config import YEKATERINBURG_TZ
    from pybot.core.constants import YEKATERINBURG_TZ as CONST_YEKATERINBURG_TZ

    assert YEKATERINBURG_TZ.key == "Asia/Yekaterinburg"
    assert CONST_YEKATERINBURG_TZ.key == "Asia/Yekaterinburg"
