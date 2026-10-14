import pytest
from typing import Any, cast

from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from pybot.core.config import AppSettings


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


def _base_settings(**values: object) -> AppSettingsWithoutDotenv:
    return cast(Any, AppSettingsWithoutDotenv)(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=123456789,
        **values,
    )


def test_group_publications_are_disabled_without_chat_and_topic() -> None:
    settings = _base_settings()

    assert settings.achievements_chat_id is None
    assert settings.achievements_topic_id is None
    assert settings.achievements_publication_enabled is False


def test_blank_group_publication_environment_values_disable_publications() -> None:
    settings = _base_settings(ACHIEVEMENTS_CHAT_ID="", ACHIEVEMENTS_TOPIC_ID="")

    assert settings.achievements_chat_id is None
    assert settings.achievements_topic_id is None
    assert settings.achievements_publication_enabled is False


@pytest.mark.parametrize(
    "values",
    [
        {"ACHIEVEMENTS_CHAT_ID": -1001234567890},
        {"ACHIEVEMENTS_TOPIC_ID": 17},
    ],
)
def test_group_publication_settings_must_be_configured_together(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="ACHIEVEMENTS_CHAT_ID and ACHIEVEMENTS_TOPIC_ID"):
        _base_settings(**values)


def test_group_publications_accept_a_negative_group_and_positive_topic() -> None:
    settings = _base_settings(ACHIEVEMENTS_CHAT_ID=-1001234567890, ACHIEVEMENTS_TOPIC_ID=17)

    assert settings.achievements_chat_id == -1001234567890
    assert settings.achievements_topic_id == 17
    assert settings.achievements_publication_enabled is True


@pytest.mark.parametrize(
    "values",
    [
        {"ACHIEVEMENTS_CHAT_ID": 123456789, "ACHIEVEMENTS_TOPIC_ID": 17},
        {"ACHIEVEMENTS_CHAT_ID": -1001234567890, "ACHIEVEMENTS_TOPIC_ID": 0},
    ],
)
def test_group_publication_settings_validate_chat_and_topic_ids(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        _base_settings(**values)
