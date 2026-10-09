import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from pybot.core.config import AppSettings
from scripts.ci.validate_deploy_env import _validate_agent_env, _validate_orchestration_switches


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


def settings(**overrides):
    return IsolatedSettings(
        BOT_TOKEN="123456:test",
        BOT_MODE="prod",
        ROLE_REQUEST_ADMIN_TG_ID=11,
        DATABASE_URL="postgresql+asyncpg://test:test@localhost/pybot_test",
        **overrides,
    )


def test_agent_defaults_disabled_and_vision_unchanged():
    parsed = settings()
    assert not parsed.agent_enabled
    assert parsed.agent_model == "google/gemini-3.5-flash-lite"
    assert parsed.agent_memory_max_messages == 10
    assert parsed.agent_memory_ttl_s == 1800
    assert parsed.polza_vision_model == "google/gemini-2.5-flash"
    assert settings(AGENT_GROUP_CHAT_ID="", AGENT_TOPIC_ID="").agent_topic_id is None


@pytest.mark.parametrize(
    "values",
    [
        {"AGENT_ENABLED": True},
        {"AGENT_ENABLED": True, "POLZA_API_KEY": " "},
        {"AGENT_GROUP_CHAT_ID": -100},
        {"AGENT_TOPIC_ID": 3},
        {"AGENT_GROUP_CHAT_ID": 100, "AGENT_TOPIC_ID": 3},
        {"AGENT_MEMORY_MAX_MESSAGES": 11},
        {"AGENT_MEMORY_TTL_S": 0},
        {"AGENT_TIMEOUT_S": 0},
        {"AGENT_MAX_STEPS": 0},
        {"AGENT_MODEL": ""},
        {"AGENT_MODEL": " "},
    ],
)
def test_invalid_runtime_settings_rejected(values):
    with pytest.raises(ValidationError):
        settings(**values)


def test_agent_enabled_with_credentials():
    parsed = settings(AGENT_ENABLED=True, POLZA_API_KEY="test-key")
    assert parsed.agent_enabled
    assert "test-key" not in repr(parsed)


@pytest.mark.parametrize(
    "values,expected",
    [
        ({}, False),
        ({"AGENT_ENABLED": "false"}, False),
        ({"AGENT_ENABLED": "true"}, True),
        ({"AGENT_ENABLED": "true", "POLZA_API_KEY": "fake"}, False),
        ({"AGENT_GROUP_CHAT_ID": "-100"}, True),
        ({"AGENT_TOPIC_ID": "3"}, True),
        ({"AGENT_GROUP_CHAT_ID": "-100", "AGENT_TOPIC_ID": "3"}, False),
        ({"AGENT_GROUP_CHAT_ID": "100", "AGENT_TOPIC_ID": "3"}, True),
        ({"AGENT_MEMORY_MAX_MESSAGES": "11"}, True),
        ({"AGENT_MEMORY_TTL_S": "0"}, True),
        ({"AGENT_MAX_STEPS": "garbage"}, True),
        ({"AGENT_TIMEOUT_S": "301"}, True),
        ({"AGENT_MODEL": " "}, True),
        ({"AGENT_ENABLED": "unknown"}, True),
    ],
)
def test_deploy_validation_matches_runtime(values, expected):
    assert bool(_validate_agent_env(values) + _validate_orchestration_switches(values)) is expected
