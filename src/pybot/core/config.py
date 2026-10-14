from __future__ import annotations

import functools
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_extra_types.cron import CronStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .constants import RoleEnum

YEKATERINBURG_TZ = ZoneInfo("Asia/Yekaterinburg")


class AgentProviderSettings(BaseModel):
    """Explicit Polza routing; provider IDs come from the evaluated catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    order: tuple[str, ...] = ("decart/fp4", "deepseek")
    only: tuple[str, ...] = ("decart/fp4", "deepseek")
    require_parameters: bool = Field(True, strict=True)
    allow_fallbacks: bool = Field(True, strict=True)

    @field_validator("order", "only")
    @classmethod
    def validate_provider_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(name.strip() for name in names)
        if any(not name for name in normalized):
            raise ValueError("Provider names must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Provider names must be unique")
        return normalized


class AppSettings(BaseSettings):
    """Typed runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict()

    # Telegram settings
    bot_token: str = Field(..., alias="BOT_TOKEN", description="Production bot token")
    bot_mode: Literal["test", "prod"] = Field(
        "test",
        alias="BOT_MODE",
        description="Bot runtime mode: 'test' uses BOT_TOKEN_TEST, 'prod' uses BOT_TOKEN",
    )
    bot_token_test: str | None = Field(
        None,
        alias="BOT_TOKEN_TEST",
        description="Test bot token",
        validate_default=True,
    )
    notification_backend: Literal["telegram", "logging"] = Field(
        "telegram",
        alias="NOTIFICATION_BACKEND",
        description="Notification backend: 'telegram' or 'logging'",
    )
    telegram_proxy_url: str | None = Field(
        None,
        alias="TELEGRAM_PROXY_URL",
        description="Optional proxy URL for Telegram Bot API traffic",
    )
    runtime_alerts_enabled: bool = Field(
        False,
        alias="RUNTIME_ALERTS_ENABLED",
        description="Enable runtime startup/shutdown alerts for the bot process",
    )
    runtime_alerts_chat_id: int | None = Field(
        None,
        alias="RUNTIME_ALERTS_CHAT_ID",
        description="Telegram chat id for runtime alerts",
        gt=0,
    )
    fsm_storage_backend: Literal["memory", "redis"] = Field(
        "redis",
        alias="FSM_STORAGE_BACKEND",
        description="FSM storage backend: 'memory' or 'redis'",
    )
    redis_url: str = Field(
        "redis://localhost:6379/0",
        alias="REDIS_URL",
        description="Redis URL used for FSM storage when FSM_STORAGE_BACKEND=redis",
    )
    # TODO костыль до поддержки multi-instance
    role_request_admin_tg_id: int = Field(
        ...,
        alias="ROLE_REQUEST_ADMIN_TG_ID",
        description="Telegram user id of admin recipient for role requests",
        gt=0,
    )
    role_request_reject_cooldown_minutes: int = Field(
        1440,
        alias="ROLE_REQUEST_REJECT_COOLDOWN_MINUTES",
        description="Cooldown in minutes for role request rejection",
        gt=0,
    )

    auto_admin_telegram_ids: set[int] = Field(
        default_factory=set,
        alias="AUTO_ADMIN_TELEGRAM_IDS",
        description="Telegram user ids that receive Admin role automatically on registration",
    )

    # Database settings
    database_url: str = Field(..., alias="DATABASE_URL", description="Database URL")

    # General settings
    log_level: str = Field("INFO", alias="LOG_LEVEL", description="Logging level")
    log_format: Literal["text", "json"] = Field(
        "text",
        alias="LOG_FORMAT",
        description=(
            "Log output format. If LOG_FORMAT is not set explicitly, runtime defaults are applied: "
            "'text' for local/dev (`BOT_MODE=test`) and 'json' for production (`BOT_MODE=prod`). "
            "Use 'json' for structured machine-readable output with log collectors such as Loki, "
            "CloudWatch, or ELK."
        ),
    )
    debug: bool = Field(False, alias="DEBUG", description="Debug mode")

    # Rate limit settings
    enable_rate_limit: bool = Field(True, alias="ENABLE_RATE_LIMIT")
    rate_limit_cheap: int = Field(30, alias="RATE_LIMIT_CHEAP")
    time_limit_cheap: int = Field(60, alias="TIME_LIMIT_CHEAP")
    rate_limit_moderate: int = Field(10, alias="RATE_LIMIT_MODERATE")
    time_limit_moderate: int = Field(60, alias="TIME_LIMIT_MODERATE")
    rate_limit_expensive: int = Field(3, alias="RATE_LIMIT_EXPENSIVE")
    time_limit_expensive: int = Field(300, alias="TIME_LIMIT_EXPENSIVE")
    max_user_limiters: int = Field(1000, alias="MAX_USER_LIMITERS", description="Limiter cache size")

    # Broadcast settings
    broadcast_bulk_size: int = Field(20, alias="BROADCAST_BULK_SIZE", ge=1, le=25)
    broadcast_max_concurrency: int = Field(5, alias="BROADCAST_MAX_CONCURRENCY", ge=1, le=10)
    broadcast_batch_pause_ms: int = Field(1200, alias="BROADCAST_BATCH_PAUSE_MS", ge=700, le=5000)
    broadcast_jitter_min_ms: int = Field(80, alias="BROADCAST_JITTER_MIN_MS", ge=50, le=1000)
    broadcast_jitter_max_ms: int = Field(160, alias="BROADCAST_JITTER_MAX_MS", ge=50, le=2000)
    broadcast_retry_attempts: int = Field(5, alias="BROADCAST_RETRY_ATTEMPTS", ge=1, le=10)
    broadcast_retry_max_wait_s: int = Field(30, alias="BROADCAST_RETRY_MAX_WAIT_S", ge=1, le=120)
    leaderboard_weekly_enabled: bool = Field(
        False,
        alias="LEADERBOARD_WEEKLY_ENABLED",
        description="Enable weekly leaderboard publication via TaskIQ scheduler",
    )
    leaderboard_weekly_recipient_id: int | None = Field(
        None,
        alias="LEADERBOARD_WEEKLY_RECIPIENT_ID",
        description="Transport recipient id for weekly leaderboard publication",
    )

    leaderboard_weekly_thread_id: int | None = Field(
        None,
        alias="LEADERBOARD_WEEKLY_THREAD_ID",
        description="Transport message thread id for weekly leaderboard publication",
    )

    leaderboard_weekly_cron: CronStr = Field(
        default_factory=lambda: CronStr("0 9 * * 1"),
        alias="LEADERBOARD_WEEKLY_CRON",
        description="Cron expression for weekly leaderboard publication",
    )
    leaderboard_weekly_timezone: TimeZoneName = Field(
        default_factory=lambda: TimeZoneName("Asia/Yekaterinburg"),
        alias="LEADERBOARD_WEEKLY_TIMEZONE",
        description="Timezone used for weekly leaderboard cron scheduling",
    )
    leaderboard_weekly_limit: int = Field(
        10,
        alias="LEADERBOARD_WEEKLY_LIMIT",
        description="Max rows per leaderboard section in weekly publication",
        ge=1,
    )
    leaderboard_weekly_retry_enabled: bool = Field(
        True,
        alias="LEADERBOARD_WEEKLY_RETRY_ENABLED",
        description="Enable retry labels for weekly leaderboard task execution",
    )
    leaderboard_weekly_retry_max_retries: int = Field(
        3,
        alias="LEADERBOARD_WEEKLY_RETRY_MAX_RETRIES",
        description="Maximum retry attempts for weekly leaderboard task",
        ge=1,
    )
    leaderboard_weekly_retry_delay_s: int = Field(
        30,
        alias="LEADERBOARD_WEEKLY_RETRY_DELAY_S",
        description="Base retry delay in seconds for weekly leaderboard task",
        ge=1,
    )
    leaderboard_weekly_retry_use_jitter: bool = Field(
        True,
        alias="LEADERBOARD_WEEKLY_RETRY_USE_JITTER",
        description="Use jitter for weekly leaderboard retry delays",
    )
    leaderboard_weekly_retry_use_exponential_backoff: bool = Field(
        True,
        alias="LEADERBOARD_WEEKLY_RETRY_USE_EXPONENTIAL_BACKOFF",
        description="Use exponential backoff for weekly leaderboard retry delays",
    )
    leaderboard_weekly_retry_max_delay_s: int = Field(
        300,
        alias="LEADERBOARD_WEEKLY_RETRY_MAX_DELAY_S",
        description="Maximum retry delay in seconds for weekly leaderboard task",
        ge=1,
    )

    achievements_chat_id: int | None = Field(
        None,
        alias="ACHIEVEMENTS_CHAT_ID",
        description="Optional Telegram group chat for community achievement publications",
        lt=0,
    )
    achievements_topic_id: int | None = Field(
        None,
        alias="ACHIEVEMENTS_TOPIC_ID",
        description="Optional Telegram topic for community achievement publications",
        gt=0,
    )

    # Middleware toggles
    enable_logging_middleware: bool = Field(
        True,
        alias="ENABLE_LOGGING_MIDDLEWARE",
        description="Enable logging middleware",
    )
    enable_user_activity_middleware: bool = Field(
        True,
        alias="ENABLE_USER_ACTIVITY_MIDDLEWARE",
        description="Enable user activity middleware",
    )
    enable_role_middleware: bool = Field(
        True,
        alias="ENABLE_ROLE_MIDDLEWARE",
        description="Enable role-check middleware",
    )

    # Health API settings
    health_api_enabled: bool = Field(
        False,
        alias="HEALTH_API_ENABLED",
        description="Enable separate FastAPI health endpoints process",
    )
    health_api_orchestration_enabled: bool | None = Field(
        None,
        alias="HEALTH_API_ORCHESTRATION_ENABLED",
        description=(
            "Original orchestration flag for the dedicated health process. "
            "Used by bot-side notifications when the bot runtime overrides HEALTH_API_ENABLED=false "
            "to prevent accidental in-process startup."
        ),
    )
    health_api_host: str = Field(
        "localhost",
        alias="HEALTH_API_HOST",
        description="Health API host",
    )
    health_api_port: int = Field(
        8001,
        alias="HEALTH_API_PORT",
        description="Health API port",
    )

    broadcast_allowed_roles: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {"Admin"},
        alias="BROADCAST_ALLOWED_ROLES",
        description="Broadcast allowed roles",
    )

    broadcast_max_text_length: int = Field(
        4093,
        alias="BROADCAST_MAX_TEXT_LENGTH",
        description="Broadcast body length before adding crop suffix",
        ge=1,
        le=4096,
    )

    # Agent harness (opt-in; uses Polza credentials independently of vision)
    agent_enabled: bool = Field(False, alias="AGENT_ENABLED")
    agent_model: str = Field("deepseek/deepseek-v4.1-flash", alias="AGENT_MODEL", min_length=1)
    agent_provider: AgentProviderSettings = Field(default_factory=AgentProviderSettings, alias="AGENT_PROVIDER")
    agent_reasoning_effort: Literal["max", "xhigh", "high", "medium", "low", "minimal", "none"] = Field(
        "none", alias="AGENT_REASONING_EFFORT"
    )
    agent_group_chat_id: int | None = Field(None, alias="AGENT_GROUP_CHAT_ID", lt=0)
    agent_topic_id: int | None = Field(None, alias="AGENT_TOPIC_ID", gt=0)
    agent_memory_max_messages: int = Field(10, alias="AGENT_MEMORY_MAX_MESSAGES", ge=2, le=10)
    agent_memory_ttl_s: int = Field(1800, alias="AGENT_MEMORY_TTL_S", ge=1, le=1800)
    agent_reply_mark_ttl_s: int = Field(604800, alias="AGENT_REPLY_MARK_TTL_S", ge=1)
    agent_max_steps: int = Field(8, alias="AGENT_MAX_STEPS", ge=1, le=20)
    agent_timeout_s: int = Field(60, alias="AGENT_TIMEOUT_S", ge=5, le=300)

    # Polza.ai and Attendance settings
    polza_api_key: SecretStr | None = Field(None, alias="POLZA_API_KEY", description="Polza.ai API key")
    polza_base_url: str = Field("https://polza.ai/api/v1", alias="POLZA_BASE_URL", description="Polza.ai API base URL")
    polza_vision_model: str = Field(
        "google/gemini-2.5-flash",
        alias="POLZA_VISION_MODEL",
        description="Vision model for attendance sheet recognition",
    )
    polza_timeout_s: int = Field(
        60,
        alias="POLZA_TIMEOUT_S",
        ge=5,
        le=300,
        description="Polza.ai request timeout in seconds",
    )
    attendance_timezone: TimeZoneName = Field(
        default_factory=lambda: TimeZoneName("Asia/Yekaterinburg"),
        alias="ATTENDANCE_TIMEZONE",
        description="Timezone for attendance accounting",
    )
    attendance_stale_draft_cleanup_enabled: bool = Field(
        True,
        alias="ATTENDANCE_STALE_DRAFT_CLEANUP_ENABLED",
        description="Enable periodic cleanup of stale attendance drafts",
    )
    attendance_stale_draft_cleanup_cron: CronStr = Field(
        default_factory=lambda: CronStr("0 * * * *"),
        alias="ATTENDANCE_STALE_DRAFT_CLEANUP_CRON",
        description="Cron schedule for stale draft cleanup",
    )

    @model_validator(mode="after")
    def validate_agent_config(self) -> Self:
        """Require credentials only when enabled and keep topic configuration coherent."""
        if (self.agent_group_chat_id is None) != (self.agent_topic_id is None):
            raise ValueError("AGENT_GROUP_CHAT_ID and AGENT_TOPIC_ID must be configured together")
        if self.agent_enabled and (self.polza_api_key is None or not self.polza_api_key.get_secret_value().strip()):
            raise ValueError("POLZA_API_KEY must be set when AGENT_ENABLED=true")
        return self

    @field_validator("agent_model")
    @classmethod
    def normalize_agent_model(cls, value: str) -> str:
        """Reject blank model identifiers before contacting the provider."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("AGENT_MODEL must be non-empty")
        return normalized

    @field_validator("agent_group_chat_id", "agent_topic_id", mode="before")
    @classmethod
    def parse_agent_optional_id(cls, value: int | str | None) -> int | None:
        """Allow blank optional IDs in .env.example."""
        if isinstance(value, str) and not value.strip():
            return None
        return int(value) if value is not None else None

    @property
    def active_bot_token(self: Self) -> str:
        """Return active bot token based on BOT_MODE."""
        if self.bot_mode == "prod":
            return self.bot_token
        if self.bot_token_test is None:
            raise ValueError("BOT_TOKEN_TEST must be set when BOT_MODE is not 'prod'")
        return self.bot_token_test

    @property
    def runtime_alerts_health_api_enabled(self: Self) -> bool:
        """Return the health-process status that should be reported in bot runtime alerts."""
        if self.health_api_orchestration_enabled is not None:
            return self.health_api_orchestration_enabled
        return self.health_api_enabled

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_flag(cls, value: bool | str | int) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            return value != 0

        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on", "debug"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off", "release"}:
            return False

        raise ValueError("DEBUG must be a boolean-like value (e.g. true/false/debug/release)")

    @field_validator("telegram_proxy_url", mode="before")
    @classmethod
    def parse_telegram_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("bot_token_test", mode="after")
    @classmethod
    def validate_test_bot_token(cls, value: str | None, info: ValidationInfo) -> str | None:
        bot_mode = info.data.get("bot_mode", "test")
        if bot_mode == "prod":
            return value

        if value is None:
            raise ValueError("BOT_TOKEN_TEST must be set when BOT_MODE is not 'prod'")

        normalized = value.strip()
        if not normalized:
            raise ValueError("BOT_TOKEN_TEST must be set when BOT_MODE is not 'prod'")
        return normalized

    @field_validator("bot_token", mode="after")
    @classmethod
    def validate_prod_bot_token(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("BOT_TOKEN must be set")
        return normalized

    @field_validator("leaderboard_weekly_recipient_id", mode="before")
    @classmethod
    def parse_weekly_recipient_id(cls, value: int | str | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            return int(normalized)
        return value

    @field_validator("achievements_chat_id", "achievements_topic_id", mode="before")
    @classmethod
    def parse_optional_achievement_id(cls, value: int | str | None) -> int | None:
        """Allow empty publication IDs in local and production environment files."""
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return int(normalized) if normalized else None
        return value

    @field_validator("leaderboard_weekly_recipient_id", "leaderboard_weekly_thread_id")
    @classmethod
    def validate_weekly_ids(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("Weekly IDs must not be equal to 0")

        return value

    @field_validator("broadcast_allowed_roles", mode="before")
    @classmethod
    def parse_broadcast_allowed_roles(cls, value: object) -> set[str]:
        if isinstance(value, set):
            roles = {str(role).strip() for role in value if str(role).strip()}
            return cls._validate_roles(roles)

        if isinstance(value, list | tuple):
            roles = {str(role).strip() for role in value if str(role).strip()}
            return cls._validate_roles(roles)

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return set()

            # Supports both "Admin,Mentor" and JSON-like '["Admin","Mentor"]'.
            normalized = normalized.strip("[]")
            tokens = (token.strip().strip("\"'") for token in normalized.split(","))
            roles = {token for token in tokens if token}
            return cls._validate_roles(roles)

        raise ValueError("BROADCAST_ALLOWED_ROLES must be a comma-separated string or a sequence")

    @staticmethod
    def _validate_roles(roles: set[str]) -> set[str]:
        available_roles = {role.value for role in RoleEnum}
        unknown_roles = roles - available_roles
        if unknown_roles:
            sorted_roles = ", ".join(sorted(unknown_roles))
            raise ValueError(f"Unknown role(s) in BROADCAST_ALLOWED_ROLES: {sorted_roles}")
        return roles

    @model_validator(mode="after")
    def apply_runtime_log_format_default(self: Self) -> Self:
        if "log_format" in self.model_fields_set:
            return self

        self.log_format = "json" if self.bot_mode == "prod" else "text"
        return self

    @model_validator(mode="after")
    def validate_broadcast_jitter_range(self: Self) -> Self:
        if self.broadcast_jitter_max_ms < self.broadcast_jitter_min_ms:
            raise ValueError("BROADCAST_JITTER_MAX_MS must be greater than or equal to BROADCAST_JITTER_MIN_MS")
        return self

    @model_validator(mode="after")
    def validate_runtime_alerts_config(self: Self) -> Self:
        if self.runtime_alerts_enabled and self.runtime_alerts_chat_id is None:
            raise ValueError("RUNTIME_ALERTS_CHAT_ID must be set when RUNTIME_ALERTS_ENABLED=true")
        return self

    @model_validator(mode="after")
    def validate_weekly_leaderboard_config(self: Self) -> Self:
        if self.leaderboard_weekly_enabled and self.leaderboard_weekly_recipient_id is None:
            raise ValueError("LEADERBOARD_WEEKLY_RECIPIENT_ID must be set when LEADERBOARD_WEEKLY_ENABLED=true")
        return self

    @model_validator(mode="after")
    def validate_weekly_leaderboard_retry_config(self: Self) -> Self:
        if self.leaderboard_weekly_retry_max_delay_s < self.leaderboard_weekly_retry_delay_s:
            raise ValueError(
                "LEADERBOARD_WEEKLY_RETRY_MAX_DELAY_S must be greater than or equal to LEADERBOARD_WEEKLY_RETRY_DELAY_S"
            )
        return self

    @model_validator(mode="after")
    def validate_achievement_publication_config(self: Self) -> Self:
        if (self.achievements_chat_id is None) != (self.achievements_topic_id is None):
            raise ValueError("ACHIEVEMENTS_CHAT_ID and ACHIEVEMENTS_TOPIC_ID must be configured together")
        return self

    @property
    def achievements_publication_enabled(self: Self) -> bool:
        """Return whether the complete group publication destination is configured."""
        return self.achievements_chat_id is not None and self.achievements_topic_id is not None


@functools.lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the application settings as a cached singleton."""

    class _BootstrapAppSettings(AppSettings):
        @staticmethod
        def _runtime_env_keys(settings_cls: type[BaseSettings]) -> set[str]:
            runtime_env_keys = set(settings_cls.model_fields)
            for field_info in settings_cls.model_fields.values():
                alias = field_info.alias
                validation_alias = field_info.validation_alias

                if isinstance(alias, str):
                    runtime_env_keys.add(alias)
                if isinstance(validation_alias, str):
                    runtime_env_keys.add(validation_alias)

            return runtime_env_keys

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            del dotenv_settings

            class _RuntimeDotEnvSettingsSource(DotEnvSettingsSource):
                def __call__(self) -> dict[str, object]:
                    dotenv_values = super().__call__()
                    runtime_env_keys = _BootstrapAppSettings._runtime_env_keys(settings_cls)
                    return {key: value for key, value in dotenv_values.items() if key in runtime_env_keys}

            return (
                init_settings,
                env_settings,
                _RuntimeDotEnvSettingsSource(
                    settings_cls,
                    env_file=".env",
                    env_file_encoding="utf-8",
                ),
                file_secret_settings,
            )

    return _BootstrapAppSettings()
