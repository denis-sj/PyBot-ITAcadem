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
    assert parsed.agent_model == "deepseek/deepseek-v4.1-flash"
    assert parsed.agent_provider.order == ("decart/fp4", "deepseek")
    assert parsed.agent_provider.only == ("decart/fp4", "deepseek")
    assert parsed.agent_provider.require_parameters is True
    assert parsed.agent_provider.allow_fallbacks is True
    assert parsed.agent_memory_max_messages == 10
    assert parsed.agent_memory_ttl_s == 1800
    assert parsed.agent_reply_mark_ttl_s == 604800
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
        {"AGENT_REPLY_MARK_TTL_S": 0},
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
        ({"AGENT_REPLY_MARK_TTL_S": "0"}, True),
        ({"AGENT_REPLY_MARK_TTL_S": "invalid"}, True),
        ({"AGENT_REPLY_MARK_TTL_S": "604800"}, False),
        ({"AGENT_MAX_STEPS": "garbage"}, True),
        ({"AGENT_TIMEOUT_S": "301"}, True),
        ({"AGENT_MODEL": " "}, True),
        ({"AGENT_ENABLED": "unknown"}, True),
    ],
)
def test_deploy_validation_matches_runtime(values, expected):
    assert bool(_validate_agent_env(values) + _validate_orchestration_switches(values)) is expected


@pytest.mark.parametrize(
    "provider",
    [
        {"only": [""]},
        {"order": ["deepseek", "deepseek"]},
        {"unsupported": True},
    ],
)
def test_invalid_provider_settings_rejected_by_runtime_and_deploy(provider):
    import json

    with pytest.raises(ValidationError):
        settings(AGENT_PROVIDER=provider)
    assert _validate_agent_env({"AGENT_PROVIDER": json.dumps(provider)})


def test_custom_provider_routing_and_deploy_validation():
    import json

    provider = {"order": ["deepseek", "decart/fp4"], "only": [], "require_parameters": True, "allow_fallbacks": True}
    parsed = settings(AGENT_PROVIDER=provider)
    assert parsed.agent_provider.order == ("deepseek", "decart/fp4")
    assert parsed.agent_provider.only == ()
    assert not _validate_agent_env({"AGENT_PROVIDER": json.dumps(provider)})


@pytest.mark.parametrize("effort", ["max", "xhigh", "high", "medium", "low", "minimal", "none"])
def test_reasoning_efforts_match_runtime_and_deploy(effort):
    assert settings(AGENT_REASONING_EFFORT=effort).agent_reasoning_effort == effort
    assert not _validate_agent_env({"AGENT_REASONING_EFFORT": effort})


@pytest.mark.parametrize("effort", ["", "disabled", "NONE"])
def test_invalid_reasoning_effort_is_rejected(effort):
    with pytest.raises(ValidationError):
        settings(AGENT_REASONING_EFFORT=effort)
    assert _validate_agent_env({"AGENT_REASONING_EFFORT": effort})


def test_reasoning_defaults_disabled():
    assert settings().agent_reasoning_effort == "none"
