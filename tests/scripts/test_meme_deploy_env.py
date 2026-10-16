import pytest

from scripts.ci.validate_deploy_env import _validate_meme_env, _validate_orchestration_switches


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"MEME_ENABLED": "false"},
        {
            "MEME_ENABLED": "true",
            "MEME_CHAT_ID": "-100123",
            "MEME_TOPIC_ID": "42",
            "MEME_COMMUNITIES": "programmer_humor@programming.dev,linuxmemes@lemmy.world",
            "MEME_INSTANCES": "https://programming.dev,https://lemmy.world/",
        },
    ],
)
def test_valid_meme_deploy_values(values: dict[str, str]) -> None:
    assert _validate_meme_env(values) == []
    assert _validate_orchestration_switches(values) == []


@pytest.mark.parametrize(
    "values",
    [
        {"MEME_ENABLED": "true"},
        {"MEME_CHAT_ID": "-100123"},
        {"MEME_TOPIC_ID": "42"},
        {"MEME_ENABLED": "perhaps"},
        {"MEME_ENABLED": "true", "MEME_CHAT_ID": "100", "MEME_TOPIC_ID": "42"},
        {"MEME_ENABLED": "true", "MEME_CHAT_ID": "-100123", "MEME_TOPIC_ID": "0"},
        {"MEME_MIN_SCORE": "-1"},
        {"MEME_MAX_REPLACEMENTS_PER_WEEK": "4"},
        {"MEME_COMMUNITIES": "not-a-community"},
        {"MEME_INSTANCES": "http://lemmy.world"},
        {"MEME_INSTANCES": "https://user:password@lemmy.world"},
        {"MEME_INSTANCES": "https://lemmy.world/api/v3"},
    ],
)
def test_invalid_meme_deploy_values_are_rejected(values: dict[str, str]) -> None:
    assert _validate_meme_env(values) or _validate_orchestration_switches(values)
