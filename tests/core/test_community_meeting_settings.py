"""Validation for optional meeting publication destinations."""

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from typing import Any, cast

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


def test_meeting_publication_destinations_are_disabled_when_unset_or_blank() -> None:
    settings = _base_settings(EVENTS_CHAT_ID="", EVENTS_TOPIC_ID="")

    assert settings.events_chat_id is None
    assert settings.events_topic_id is None


@pytest.mark.parametrize(
    "values",
    [
        {"EVENTS_CHAT_ID": -1001234567890},
        {"EVENTS_TOPIC_ID": 17},
    ],
)
def test_meeting_publication_destination_requires_both_ids(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="EVENTS_CHAT_ID and EVENTS_TOPIC_ID"):
        _base_settings(**values)


def test_meeting_publication_destination_validates_group_and_topic_ids() -> None:
    with pytest.raises(ValidationError):
        _base_settings(EVENTS_CHAT_ID=123456789, EVENTS_TOPIC_ID=17)
    with pytest.raises(ValidationError):
        _base_settings(EVENTS_CHAT_ID=-1001234567890, EVENTS_TOPIC_ID=0)


def test_meeting_publication_destination_accepts_negative_chat_and_positive_topic() -> None:
    settings = _base_settings(EVENTS_CHAT_ID=-1001234567890, EVENTS_TOPIC_ID=17)

    assert settings.events_chat_id == -1001234567890
    assert settings.events_topic_id == 17


def test_events_topic_name_is_trimmed_for_previews() -> None:
    settings = _base_settings(EVENTS_TOPIC_NAME="  Python-встречи  ")

    assert settings.events_topic_name == "Python-встречи"


def test_events_topic_name_cannot_be_blank() -> None:
    with pytest.raises(ValidationError, match="EVENTS_TOPIC_NAME must be non-empty"):
        _base_settings(EVENTS_TOPIC_NAME="   ")
