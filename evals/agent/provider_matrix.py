"""Budgeted synthetic compatibility matrix; no tools are executed."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from pybot.core.config import get_settings
from pybot.services.agent.tools import AgentToolRegistry

from .pricing import ProviderCatalog
from .run import (
    MAX_BUDGET_RUB,
    REPORTS_DIR,
    TARGET_MODEL,
    BudgetStopError,
    ObservedPolzaModel,
    SpendLedger,
    _build_eval_model,
    _safe_http_metadata,
)

QUESTION = "Покажи мой профиль."
OUTPUT_LIMIT = 300


@dataclass(frozen=True)
class ProbeVariant:
    tools: str
    reasoning: str
    route: str
    provider: dict[str, JsonValue] | None
    parallel_tool_calls: bool | None = False


async def main() -> None:
    """Persist each probe before moving on, including redacted provider errors."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, choices=(2, 3), default=2)
    parser.add_argument("--route", action="append")
    parser.add_argument("--tools", action="append")
    parser.add_argument("--omit-parallel", action="store_true")
    args = parser.parse_args()
    catalog = ProviderCatalog.load(REPORTS_DIR / "provider-catalog.json")
    ledger = SpendLedger()
    report_path = REPORTS_DIR / "provider-matrix.json"
    records: list[dict[str, JsonValue]] = json.loads(report_path.read_text())["records"] if report_path.exists() else []
    for variant in _variants(catalog, args.route, args.tools, args.omit_parallel):
        for repetition in range(1, args.repeat + 1):
            try:
                record = await _probe(variant, repetition, catalog, ledger)
            except BudgetStopError:
                _save_reports(report_path, records, catalog, ledger, stopped=True)
                print("STOP: insufficient remaining budget for a bounded request")
                return
            records.append(record)
            _save_reports(report_path, records, catalog, ledger)
            print(json.dumps(record, ensure_ascii=False), flush=True)


def _variants(
    catalog: ProviderCatalog,
    selected_routes: list[str] | None,
    selected_tools: list[str] | None,
    omit_parallel: bool = False,
) -> list[ProbeVariant]:
    cheapest = min(
        catalog.providers,
        key=lambda provider: provider.pricing.prompt_per_million + provider.pricing.completion_per_million,
    )
    native = next(provider for provider in catalog.providers if provider.name == "deepseek")
    routes: dict[str, dict[str, JsonValue] | None] = {
        "auto": None,
        "only_cheapest": {"require_parameters": True, "only": [cheapest.name], "allow_fallbacks": False},
        "only_native": {"require_parameters": True, "only": [native.name], "allow_fallbacks": False},
        "order_cheapest_native": {
            "require_parameters": True,
            "order": [cheapest.name, native.name],
            "allow_fallbacks": False,
        },
    }
    return [
        ProbeVariant(tools, reasoning, route, provider, None if omit_parallel else False)
        for route, provider in routes.items()
        if selected_routes is None or route in selected_routes
        for tools in ("none", "simple", "student", "admin")
        if selected_tools is None or tools in selected_tools
        for reasoning in ("default", "disabled")
    ]


async def _probe(
    variant: ProbeVariant, repetition: int, catalog: ProviderCatalog, ledger: SpendLedger
) -> dict[str, JsonValue]:
    settings = get_settings().model_copy(update={"agent_model": TARGET_MODEL})
    captured: dict[str, JsonValue] = {}
    observer: ObservedPolzaModel | None = None

    async def capture(response: httpx2.Response) -> None:
        await response.aread()
        captured.update(_safe_http_metadata(response))
        try:
            body = response.json()
        except ValueError:
            captured["error_body"] = _redact(response.text, settings.polza_api_key.get_secret_value())
            return
        provider = body.get("provider") if isinstance(body, dict) else None
        if isinstance(provider, str) and observer is not None:
            observer.response_tariff = catalog.tariff(provider)
            captured["actual_provider"] = provider
        if isinstance(body, dict) and "error" in body:
            captured["error_body"] = _redact(
                json.dumps(body, ensure_ascii=False), settings.polza_api_key.get_secret_value()
            )

    async with httpx2.AsyncClient(timeout=settings.agent_timeout_s, event_hooks={"response": [capture]}) as client:
        observer = ObservedPolzaModel(
            _build_eval_model(settings, client), TARGET_MODEL, ledger, MAX_BUDGET_RUB, catalog.tariff()
        )
        messages: list[ModelMessage] = [
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        "Используй get_my_profile, если инструмент доступен. Иначе сообщи, что профиль недоступен."
                    ),
                    UserPromptPart(QUESTION),
                ]
            )
        ]
        model_settings = _model_settings(variant)
        started = time.perf_counter()
        try:
            response = await observer.request(messages, model_settings, _tools(variant.tools))
            captured["tool_calls"] = [part.tool_name for part in response.parts if isinstance(part, ToolCallPart)]
        except BudgetStopError:
            raise
        except Exception as error:
            captured["error_type"] = type(error).__name__
            if isinstance(error, ModelHTTPError):
                captured["status"] = error.status_code
        elapsed = time.perf_counter() - started
    entry = ledger.entries[-1]
    return {
        "tools": variant.tools,
        "reasoning": variant.reasoning,
        "route": variant.route,
        "provider_request": variant.provider,
        "parallel_tool_calls": variant.parallel_tool_calls,
        "repeat": repetition,
        "elapsed_s": round(elapsed, 3),
        "cost_rub": entry["cost_rub"],
        "estimated": entry["estimated"],
        "tariff": observer.response_tariff.model_dump(mode="json"),
        "http": captured,
    }


def _model_settings(variant: ProbeVariant) -> ModelSettings:
    model_settings: ModelSettings = {"temperature": 0, "max_tokens": OUTPUT_LIMIT}
    if variant.parallel_tool_calls is not None:
        model_settings["parallel_tool_calls"] = variant.parallel_tool_calls
    extra_body: dict[str, JsonValue] = {}
    if variant.provider is not None:
        extra_body["provider"] = variant.provider
    if variant.reasoning == "disabled":
        extra_body["reasoning"] = {"effort": "none"}
    if extra_body:
        model_settings["extra_body"] = extra_body
    return model_settings


def _tools(mode: str) -> ModelRequestParameters:
    if mode == "none":
        return ModelRequestParameters()
    if mode == "simple":
        return ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name="get_my_profile",
                    description="Получить мой профиль.",
                    parameters_json_schema={"type": "object", "properties": {}},
                )
            ]
        )
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    tools = registry.for_roles(frozenset({mode.capitalize()})).tools.values()
    return ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name=tool.name,
                description=tool.description or "",
                parameters_json_schema=tool.function_schema.json_schema,
            )
            for tool in tools
        ]
    )


def _redact(text: str, secret: str) -> str:
    """Remove credentials from provider errors before writing them to disk or stdout."""
    redacted = text.replace(secret, "[REDACTED]") if secret else text
    return re.sub(r"(?i)(bearer\s+\S+|sk-[a-z0-9_-]+)", "[REDACTED]", redacted)


def _save_reports(
    path: Path,
    records: list[dict[str, JsonValue]],
    catalog: ProviderCatalog,
    ledger: SpendLedger,
    *,
    stopped: bool = False,
) -> None:
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "model": TARGET_MODEL,
        "question": QUESTION,
        "max_tokens": OUTPUT_LIMIT,
        "retries": 0,
        "budget_limit_rub": str(MAX_BUDGET_RUB),
        "cumulative_cost_rub": str(ledger.total_cost_rub),
        "estimated_records": sum(entry.get("estimated") is True for entry in ledger.entries),
        "exact_records": sum(entry.get("estimated") is False for entry in ledger.entries),
        "stopped": stopped,
        "catalog_tariffs": catalog.model_dump(mode="json"),
        "records": records,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = [
        "# Диагностика provider",
        "",
        "Все вопросы и инструменты синтетические; инструменты не выполняются.",
        "",
        "| Tools | Reasoning | Route | Повтор | HTTP | Время, с | ₽ | Оценка | Ошибка |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for record in records:
        http = record["http"]
        if not isinstance(http, dict):
            raise TypeError("Invalid diagnostic HTTP metadata")
        error_body = str(http.get("error_body", "—")).replace("|", "\\|").replace("\n", " ")
        rows.append(
            f"| {record['tools']} | {record['reasoning']} | {record['route']} | {record['repeat']} | "
            f"{http.get('status', 'timeout')} | {record['elapsed_s']} | {record['cost_rub']} | "
            f"{record['estimated']} | {error_body} |"
        )
    path.with_suffix(".md").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
