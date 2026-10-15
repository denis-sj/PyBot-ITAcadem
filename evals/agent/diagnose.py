"""One budgeted synthetic request to isolate provider availability from agent tools."""

import argparse
import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress

import httpx2
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from pybot.core.config import get_settings
from pybot.services.agent.service import build_agent_instructions
from pybot.services.agent.tools import AgentToolRegistry

from .pricing import ProviderCatalog
from .run import (
    MAX_BUDGET_RUB,
    REPORTS_DIR,
    TARGET_MODEL,
    ObservedPolzaModel,
    SpendLedger,
    _build_eval_model,
    _safe_http_metadata,
)


async def main() -> None:
    """Send no personal data and report only fixed diagnostic categories and billing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-tools", action="store_true")
    parser.add_argument("--parallel-false", action="store_true")
    parser.add_argument("--agent-tools", action="store_true")
    parser.add_argument("--agent-instructions", action="store_true")
    parser.add_argument("--ask-profile", action="store_true")
    parser.add_argument("--thinking-disabled", action="store_true")
    args = parser.parse_args()
    parameters = ModelRequestParameters()
    if args.with_tools:
        parameters = ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name="synthetic_date",
                    description="Вернуть сегодняшнюю дату.",
                    parameters_json_schema={"type": "object", "properties": {}},
                )
            ]
        )
    model_settings = {"temperature": 0, "max_tokens": 1000}
    if args.agent_tools:
        tools = AgentToolRegistry().for_roles(frozenset({"Student"})).tools.values()
        parameters = ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name=tool.name,
                    description=tool.description or "",
                    parameters_json_schema=tool.function_schema.json_schema,
                )
                for tool in tools
            ]
        )
    if args.parallel_false:
        model_settings["parallel_tool_calls"] = False
    if args.thinking_disabled:
        model_settings["extra_body"] = {"thinking": {"type": "disabled"}}
    catalog = ProviderCatalog.load(REPORTS_DIR / "provider-catalog.json")
    settings = get_settings().model_copy(update={"agent_model": TARGET_MODEL})
    metadata = []

    async def capture(response: httpx2.Response) -> None:
        await response.aread()
        details = _safe_http_metadata(response)
        body = response.json()
        error = body.get("error") if isinstance(body, Mapping) else None
        if isinstance(error, Mapping):
            details["error_fields"] = sorted(error)
        error_text = str(error).casefold()
        details["error_terms"] = [
            term
            for term in (
                "reasoning",
                "thinking",
                "tools",
                "access",
                "token",
                "not found",
                "not allowed",
                "недоступ",
                "модель",
                "провайд",
                "ошибка",
                "не найден",
                "баланс",
                "overload",
                "busy",
            )
            if term in error_text
        ]
        metadata.append(details)

    async with httpx2.AsyncClient(timeout=settings.agent_timeout_s, event_hooks={"response": [capture]}) as client:
        model = ObservedPolzaModel(
            _build_eval_model(settings, client), TARGET_MODEL, SpendLedger(), MAX_BUDGET_RUB, catalog.tariff()
        )
        prompt_parts = [UserPromptPart("Покажи мой профиль." if args.ask_profile else "Ответь одним словом: привет.")]
        messages = [ModelRequest(parts=prompt_parts)]
        if args.agent_instructions:
            messages = [ModelRequest(parts=[SystemPromptPart(build_agent_instructions()), *prompt_parts])]
        with suppress(ModelHTTPError):
            await model.request(messages, model_settings, parameters)
        report = {"http": metadata, "cost_rub": str(model.run_cost_rub)}
        (REPORTS_DIR / "latest-diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
