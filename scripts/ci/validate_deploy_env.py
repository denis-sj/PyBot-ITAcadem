from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

_REQUIRED_KEYS = (
    "DATABASE_URL",
    "GRAFANA_ADMIN_PASSWORD",
    "NGINX_BIND_HOST",
    "NGINX_PORT",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "PUBLIC_DOMAIN",
    "TASKIQ_WORKERS",
)
_WORKER_KEY = "TASKIQ_WORKERS"
_SUPPORTED_WORKER_VALUE = "1"
_DATABASE_SCHEME = "postgresql+asyncpg"
_DATABASE_HOSTNAME = "postgres"
_ORCHESTRATION_BOOLEAN_KEYS = ("HEALTH_API_ENABLED", "AGENT_ENABLED")
_SUPPORTED_BOOLEAN_VALUES = frozenset({"0", "1", "false", "true", "no", "yes", "off", "on"})
_QUOTED_VALUE_MIN_LENGTH = 2


def parse_env_file(env_path: Path) -> dict[str, str]:
    env_values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        env_values[key.strip()] = _normalize_env_value(value.strip())
    return env_values


def validate_deploy_env(env_values: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for key in _REQUIRED_KEYS:
        if not env_values.get(key):
            errors.append(f"PROD_ENV_FILE must contain non-empty {key}=...")

    if all(env_values.get(key) for key in ("DATABASE_URL", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")):
        errors.extend(_validate_database_url(env_values))

    worker_value = env_values.get(_WORKER_KEY)
    if worker_value is not None and worker_value != _SUPPORTED_WORKER_VALUE:
        errors.append("TASKIQ_WORKERS must be set to 1 until multi-instance runtime is supported")

    errors.extend(_validate_orchestration_switches(env_values))
    errors.extend(_validate_agent_env(env_values))

    return errors


def _validate_agent_env(env_values: dict[str, str]) -> list[str]:
    """Validate agent settings without materializing runtime secrets or clients."""
    errors: list[str] = []
    if (
        env_values.get("AGENT_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        and not env_values.get("POLZA_API_KEY", "").strip()
    ):
        errors.append("POLZA_API_KEY must be set when AGENT_ENABLED=true")
    group_id = env_values.get("AGENT_GROUP_CHAT_ID", "").strip()
    topic_id = env_values.get("AGENT_TOPIC_ID", "").strip()
    if bool(group_id) != bool(topic_id):
        errors.append("AGENT_GROUP_CHAT_ID and AGENT_TOPIC_ID must be configured together")
    ranges = {
        "AGENT_GROUP_CHAT_ID": (None, -1),
        "AGENT_TOPIC_ID": (1, None),
        "AGENT_MEMORY_MAX_MESSAGES": (2, 10),
        "AGENT_MEMORY_TTL_S": (1, 1800),
        "AGENT_REPLY_MARK_TTL_S": (1, None),
        "AGENT_MAX_STEPS": (1, 20),
        "AGENT_TIMEOUT_S": (5, 300),
    }
    for key, (minimum, maximum) in ranges.items():
        value = env_values.get(key)
        if value is None or (key in {"AGENT_GROUP_CHAT_ID", "AGENT_TOPIC_ID"} and not value.strip()):
            continue
        try:
            number = int(value)
        except ValueError:
            errors.append(f"{key} must be an integer")
            continue
        if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
            errors.append(f"{key} is outside its supported range")
    if "AGENT_MODEL" in env_values and not env_values["AGENT_MODEL"].strip():
        errors.append("AGENT_MODEL must be non-empty")
    if env_values.get("AGENT_REASONING_EFFORT", "none") not in {
        "max",
        "xhigh",
        "high",
        "medium",
        "low",
        "minimal",
        "none",
    }:
        errors.append("AGENT_REASONING_EFFORT must be max, xhigh, high, medium, low, minimal or none")
    provider_json = env_values.get("AGENT_PROVIDER")
    if provider_json is not None and not _is_valid_agent_provider_json(provider_json):
        errors.append("AGENT_PROVIDER must be valid routing JSON with supported fields")
    return errors


def _is_valid_agent_provider_json(value: str) -> bool:
    """Keep deploy validation usable with the runner's standard-library Python."""
    try:
        provider = json.loads(value)
    except ValueError:
        return False
    if not isinstance(provider, dict) or set(provider) - {"order", "only", "require_parameters", "allow_fallbacks"}:
        return False
    for key in ("require_parameters", "allow_fallbacks"):
        if key in provider and not isinstance(provider[key], bool):
            return False
    for key in ("order", "only"):
        names = provider.get(key, [])
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
            return False
        normalized = [name.strip() for name in names]
        if len(set(normalized)) != len(normalized):
            return False
    return True


def _validate_database_url(env_values: dict[str, str]) -> list[str]:
    try:
        database_url = urlsplit(env_values["DATABASE_URL"])
        database_name = database_url.path.removeprefix("/")
        checks = (
            (database_url.scheme == _DATABASE_SCHEME, "DATABASE_URL must use the postgresql+asyncpg scheme"),
            (database_url.hostname == _DATABASE_HOSTNAME, "DATABASE_URL hostname must be postgres"),
            (
                unquote(database_url.username or "") == env_values["POSTGRES_USER"],
                "DATABASE_URL user does not match POSTGRES_USER",
            ),
            (
                unquote(database_url.password or "") == env_values["POSTGRES_PASSWORD"],
                "DATABASE_URL password does not match POSTGRES_PASSWORD",
            ),
            (
                unquote(database_name) == env_values["POSTGRES_DB"],
                "DATABASE_URL database does not match POSTGRES_DB",
            ),
        )
    except ValueError:
        return ["DATABASE_URL is not a valid PostgreSQL URL"]

    return [message for is_valid, message in checks if not is_valid]


def _validate_orchestration_switches(env_values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key in _ORCHESTRATION_BOOLEAN_KEYS:
        value = env_values.get(key)
        if value is None:
            continue
        if value.lower() not in _SUPPORTED_BOOLEAN_VALUES:
            errors.append(f"{key} must be one of: 1, 0, true, false, yes, no, on, off")
    return errors


def _normalize_env_value(value: str) -> str:
    if len(value) >= _QUOTED_VALUE_MIN_LENGTH and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1:
        print("Usage: validate_deploy_env.py <env-file>", file=sys.stderr)
        return 2

    env_path = Path(args[0])
    env_values = parse_env_file(env_path)
    errors = validate_deploy_env(env_values)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
