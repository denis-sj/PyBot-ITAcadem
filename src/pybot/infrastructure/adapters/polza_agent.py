"""Polza model adapter; attendance vision configuration stays independent."""

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx2
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ...core.config import AppSettings


class _PolzaChatModel(OpenAIChatModel):
    """Preserve Polza's billable usage fields in Pydantic AI response metadata."""

    def _process_provider_details(self, response: ChatCompletion) -> dict[str, Any] | None:
        provider_details = super()._process_provider_details(response) or {}
        if response.usage is None:
            return provider_details or None

        usage = response.usage
        prompt_details = usage.prompt_tokens_details
        completion_details = usage.completion_tokens_details
        extra = usage.model_extra or {}
        polza_usage: dict[str, int | str] = {
            "input_tokens": usage.prompt_tokens,
            "cached_tokens": (
                prompt_details.cached_tokens
                if prompt_details is not None and prompt_details.cached_tokens is not None
                else _polza_token_count(extra.get("prompt_cache_hit_tokens"))
            ),
            "output_tokens": usage.completion_tokens,
            "reasoning_tokens": (
                completion_details.reasoning_tokens
                if completion_details is not None and completion_details.reasoning_tokens is not None
                else _polza_token_count(extra.get("reasoning_tokens"))
            ),
        }
        cost_rub = _polza_cost_rub(extra)
        if cost_rub is not None:
            polza_usage["cost_rub"] = cost_rub
        provider_details["polza_usage"] = polza_usage
        return provider_details


def _polza_token_count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _polza_cost_rub(extra: dict[str, Any]) -> str | None:
    """Read only Polza's documented cost field, including its `cost` alias."""
    raw_cost = extra.get("cost_rub", extra.get("cost"))
    if isinstance(raw_cost, bool) or not isinstance(raw_cost, int | float | str | Decimal):
        return None
    try:
        cost = Decimal(str(raw_cost))
    except InvalidOperation:
        return None
    if not cost.is_finite() or cost < 0:
        return None
    return format(cost, "f")


class PolzaAgentModel:
    """Build Pydantic AI's OpenAI chat model with the shared Polza credentials."""

    def __init__(self, settings: AppSettings, client: httpx2.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def get_model(self) -> Model:
        """Create the model lazily so a disabled agent needs no API key."""
        key = self.settings.polza_api_key
        if key is None or not key.get_secret_value().strip():
            raise ValueError("POLZA_API_KEY is required for the agent")
        return _PolzaChatModel(
            self.settings.agent_model,
            settings={
                "extra_body": {
                    "provider": self.settings.agent_provider.model_dump(mode="json", exclude_defaults=False),
                    "reasoning": {"effort": self.settings.agent_reasoning_effort},
                }
            },
            provider=OpenAIProvider(
                openai_client=AsyncOpenAI(
                    base_url=self.settings.polza_base_url,
                    api_key=key.get_secret_value(),
                    http_client=self.client,
                    max_retries=0,
                ),
            ),
            profile=(
                OpenAIModelProfile(openai_unsupported_model_settings=("parallel_tool_calls",))
                if self.settings.agent_model.startswith("deepseek/")
                else None
            ),
        )
