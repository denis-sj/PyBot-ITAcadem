import pytest
from pydantic import ValidationError
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


def test_broadcast_jitter_range_validation() -> None:
    with pytest.raises(ValidationError, match="BROADCAST_JITTER_MAX_MS"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            BROADCAST_JITTER_MIN_MS=200,
            BROADCAST_JITTER_MAX_MS=100,
        )


def test_broadcast_bulk_size_validation() -> None:
    with pytest.raises(ValidationError, match="BROADCAST_BULK_SIZE"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            BROADCAST_BULK_SIZE=0,
        )


def test_broadcast_batch_pause_min_validation() -> None:
    with pytest.raises(ValidationError, match="BROADCAST_BATCH_PAUSE_MS"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            BROADCAST_BATCH_PAUSE_MS=600,
        )


def test_broadcast_max_text_length_validation() -> None:
    with pytest.raises(ValidationError, match="BROADCAST_MAX_TEXT_LENGTH"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            BROADCAST_MAX_TEXT_LENGTH=0,
        )


def test_auto_admin_telegram_ids_parsed_from_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_ADMIN_TELEGRAM_IDS", "[123456789,987654321,123456789]")

    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.auto_admin_telegram_ids == {123456789, 987654321}


def test_broadcast_allowed_roles_parsed_from_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROADCAST_ALLOWED_ROLES", "Admin,Mentor,Admin")

    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.broadcast_allowed_roles == {"Admin", "Mentor"}


def test_broadcast_allowed_roles_default_is_admin() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.broadcast_allowed_roles == {"Admin"}


def test_telegram_proxy_url_defaults_to_none() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.telegram_proxy_url is None


def test_telegram_proxy_url_is_parsed_from_env_var() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        TELEGRAM_PROXY_URL="socks5://127.0.0.1:1080",
    )

    assert parsed_settings.telegram_proxy_url == "socks5://127.0.0.1:1080"


def test_telegram_proxy_url_accepts_socks5h_for_remote_dns() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        TELEGRAM_PROXY_URL="socks5h://proxy.example:1080",
    )

    assert parsed_settings.telegram_proxy_url == "socks5h://proxy.example:1080"


def test_telegram_proxy_url_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValidationError, match="Supported schemes: http, socks4, socks5, socks5h"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            TELEGRAM_PROXY_URL="ftp://proxy.example:1080",
        )


def test_telegram_proxy_url_requires_host_and_port() -> None:
    with pytest.raises(ValidationError, match="must include a host and a valid port"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            TELEGRAM_PROXY_URL="socks5://proxy.example",
        )


def test_runtime_alerts_default_to_disabled_without_chat_id() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.runtime_alerts_enabled is False
    assert parsed_settings.runtime_alerts_chat_id is None


def test_runtime_alerts_chat_id_is_required_when_alerts_are_enabled() -> None:
    with pytest.raises(ValidationError, match="RUNTIME_ALERTS_CHAT_ID"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            RUNTIME_ALERTS_ENABLED=True,
        )


def test_runtime_alerts_chat_id_is_parsed_when_alerts_are_enabled() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        RUNTIME_ALERTS_ENABLED=True,
        RUNTIME_ALERTS_CHAT_ID=987654321,
    )

    assert parsed_settings.runtime_alerts_enabled is True
    assert parsed_settings.runtime_alerts_chat_id == 987654321


def test_runtime_alerts_chat_id_rejects_negative_value() -> None:
    with pytest.raises(ValidationError, match="RUNTIME_ALERTS_CHAT_ID"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            RUNTIME_ALERTS_ENABLED=True,
            RUNTIME_ALERTS_CHAT_ID=-123456789,
        )


def test_weekly_leaderboard_settings_default_to_disabled() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.leaderboard_weekly_enabled is False
    assert parsed_settings.leaderboard_weekly_recipient_id is None
    assert str(parsed_settings.leaderboard_weekly_cron) == "0 9 * * 1"
    assert str(parsed_settings.leaderboard_weekly_timezone) == "Asia/Yekaterinburg"
    assert parsed_settings.leaderboard_weekly_limit == 10
    assert parsed_settings.leaderboard_weekly_retry_enabled is True
    assert parsed_settings.leaderboard_weekly_retry_max_retries == 3
    assert parsed_settings.leaderboard_weekly_retry_delay_s == 30
    assert parsed_settings.leaderboard_weekly_retry_use_jitter is True
    assert parsed_settings.leaderboard_weekly_retry_use_exponential_backoff is True
    assert parsed_settings.leaderboard_weekly_retry_max_delay_s == 300


def test_weekly_leaderboard_recipient_is_required_when_enabled() -> None:
    with pytest.raises(ValidationError, match="LEADERBOARD_WEEKLY_RECIPIENT_ID"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            LEADERBOARD_WEEKLY_ENABLED=True,
        )


def test_weekly_leaderboard_recipient_accepts_negative_chat_id() -> None:
    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_TOKEN_TEST="123456:test",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        LEADERBOARD_WEEKLY_ENABLED=True,
        LEADERBOARD_WEEKLY_RECIPIENT_ID=-1001234567890,
    )

    assert parsed_settings.leaderboard_weekly_enabled is True
    assert parsed_settings.leaderboard_weekly_recipient_id == -1001234567890


def test_weekly_leaderboard_retry_max_delay_must_be_greater_than_or_equal_to_delay() -> None:
    with pytest.raises(ValidationError, match="LEADERBOARD_WEEKLY_RETRY_MAX_DELAY_S"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
            LEADERBOARD_WEEKLY_RETRY_DELAY_S=120,
            LEADERBOARD_WEEKLY_RETRY_MAX_DELAY_S=60,
        )


def test_broadcast_allowed_roles_rejects_unknown_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROADCAST_ALLOWED_ROLES", "Admin,WrongRole")

    with pytest.raises(ValidationError, match="Unknown role"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        )


def test_role_request_admin_tg_id_must_be_greater_than_zero() -> None:
    with pytest.raises(ValidationError, match="ROLE_REQUEST_ADMIN_TG_ID"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=0,
        )


def test_role_request_admin_tg_id_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROLE_REQUEST_ADMIN_TG_ID", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_TOKEN_TEST="123456:test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        )

    assert any(
        error["loc"] == ("ROLE_REQUEST_ADMIN_TG_ID",) and error["type"] == "missing"
        for error in exc_info.value.errors()
    )


def test_bot_token_test_is_optional_in_prod_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN_TEST", raising=False)

    parsed_settings = AppSettingsWithoutDotenv(
        BOT_TOKEN="123456:prod",
        BOT_MODE="prod",
        DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
        ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
    )

    assert parsed_settings.bot_token_test is None
    assert parsed_settings.active_bot_token == "123456:prod"


def test_bot_token_must_not_be_empty_in_prod_mode() -> None:
    with pytest.raises(ValidationError, match="BOT_TOKEN must be set"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="   ",
            BOT_MODE="prod",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        )


def test_bot_token_test_is_required_in_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN_TEST", raising=False)

    with pytest.raises(ValidationError, match="BOT_TOKEN_TEST"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            BOT_MODE="test",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        )


def test_bot_token_test_is_required_for_default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN_TEST", raising=False)

    with pytest.raises(ValidationError, match="BOT_TOKEN_TEST"):
        AppSettingsWithoutDotenv(
            BOT_TOKEN="123456:prod",
            DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
            ROLE_REQUEST_ADMIN_TG_ID=ADMIN_TG_ID,
        )
