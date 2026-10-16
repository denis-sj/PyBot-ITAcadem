import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from pybot.core.config import AppSettings


class IsolatedSettings(AppSettings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: object) -> IsolatedSettings:
    values: dict[str, object] = {
        "BOT_TOKEN": "123456:prod",
        "BOT_MODE": "prod",
        "ROLE_REQUEST_ADMIN_TG_ID": 11,
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/pybot_test",
        **overrides,
    }
    return IsolatedSettings.model_validate(values)


def test_meme_feature_defaults_disabled_with_configurable_sources() -> None:
    settings = make_settings()

    assert settings.meme_enabled is False
    assert settings.meme_publication_enabled is False
    assert settings.meme_chat_id is None
    assert settings.meme_topic_id is None
    assert settings.meme_communities == ("programmer_humor@programming.dev",)
    assert settings.meme_instances == ("https://programming.dev", "https://lemmy.world")
    assert settings.meme_min_score == 20
    assert settings.meme_max_replacements_per_week == 3


def test_meme_settings_parse_comma_separated_lists_and_destination_ids() -> None:
    settings = make_settings(
        MEME_ENABLED=True,
        MEME_CHAT_ID="-100123",
        MEME_TOPIC_ID="42",
        MEME_COMMUNITIES="programmer_humor@programming.dev,linuxmemes@lemmy.world",
        MEME_INSTANCES="https://programming.dev/,https://lemmy.world",
    )

    assert settings.meme_publication_enabled is True
    assert settings.meme_chat_id == -100123
    assert settings.meme_topic_id == 42
    assert settings.meme_communities == ("programmer_humor@programming.dev", "linuxmemes@lemmy.world")
    assert settings.meme_instances == ("https://programming.dev", "https://lemmy.world")


@pytest.mark.parametrize(
    "values",
    [
        {"MEME_ENABLED": True},
        {"MEME_CHAT_ID": -100},
        {"MEME_TOPIC_ID": 4},
        {"MEME_ENABLED": True, "MEME_CHAT_ID": -100, "MEME_TOPIC_ID": 0},
        {"MEME_COMMUNITIES": ""},
        {"MEME_COMMUNITIES": "not-a-community"},
        {"MEME_INSTANCES": "http://lemmy.world"},
        {"MEME_INSTANCES": "https://user:password@lemmy.world"},
        {"MEME_INSTANCES": "https://lemmy.world/api/v3"},
        {"MEME_MAX_REPLACEMENTS_PER_WEEK": 4},
        {"MEME_MIN_SCORE": -1},
    ],
)
def test_invalid_meme_settings_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**values)
