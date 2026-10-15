"""Opt-in Polza evaluations over deterministic synthetic agent data."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import socket
import sys
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from pathlib import Path
from statistics import mean
from typing import Literal
from unittest.mock import Mock

import httpx2
import tiktoken
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from pybot.core.config import AppSettings, get_settings
from pybot.core.constants import PointsTypeEnum
from pybot.domain.exceptions import UserNotFoundError
from pybot.dto import UserReadDTO
from pybot.dto.agent import AgentConversation, AgentMemoryMessage, AgentReply
from pybot.dto.value_objects import Points
from pybot.infrastructure.adapters.polza_agent import PolzaAgentModel
from pybot.services.agent.runtime import AgentRuntime, PendingAgentRequest
from pybot.services.agent.service import AgentService, build_agent_instructions
from pybot.services.agent.tools import AgentToolRegistry
from pybot.services.agent.usage import AgentUsageSummary, summarize_run_usage
from pybot.services.ports.agent import AgentActionsPort, AgentMemoryPort, AgentModelPort
from pybot.services.user_services.users import UserService

from .fixtures import EvalReads
from .pricing import ModelTariff, ProviderCatalog

TARGET_MODEL = "deepseek/deepseek-v4.1-flash"
MAX_BUDGET_RUB = Decimal("64.1622182716")
STOP_BUDGET_RUB = Decimal("59.1622182716")
PERCENTAGE_MULTIPLIER = 100
PREFIX_PRESSURE_PCT = 95
REQUIRED_ACCEPTANCE_REPETITIONS = 3
MINIMUM_CASE_COUNT = 30
ACTOR_TELEGRAM_ID = 1
FIRST_SYNTHETIC_USER_ID = 771234
SECOND_SYNTHETIC_USER_ID = 884422
FULL_PERCENTAGE = PERCENTAGE_MULTIPLIER
INPUT_PRICE_PER_MILLION_RUB = Decimal("5.29")
OUTPUT_PRICE_PER_MILLION_RUB = Decimal("21.17")
MAX_INPUT_TOKENS_PER_REQUEST = 32_000
MAX_ESTIMATED_INPUT_TOKENS_PER_REQUEST = 16_000
MAX_OUTPUT_TOKENS_PER_REQUEST = 1_000
REQUEST_TOKEN_OVERHEAD = 256
_EXCEPTION_REASONS: tuple[tuple[type[BaseException], str], ...] = (
    (socket.gaierror, "dns_resolution"),
    (httpx2.ConnectTimeout, "httpx_connect_timeout"),
    (httpx2.ReadTimeout, "httpx_read_timeout"),
    (httpx2.WriteTimeout, "httpx_write_timeout"),
    (httpx2.PoolTimeout, "httpx_pool_timeout"),
    (httpx2.TimeoutException, "httpx_timeout"),
    (httpx2.RemoteProtocolError, "remote_protocol_error"),
    (ConnectionResetError, "connection_reset"),
    (ConnectionAbortedError, "connection_aborted"),
    (ConnectionRefusedError, "connection_refused"),
    (BrokenPipeError, "connection_broken_pipe"),
    (httpx2.ConnectError, "httpx_connect_error"),
    (httpx2.NetworkError, "httpx_network_error"),
    (TimeoutError, "timeout_error_unclassified"),
    (asyncio.CancelledError, "request_cancelled"),
    (ModelHTTPError, "http_response_error"),
    (ConnectionError, "connection_error"),
)
_EXCEPTION_REASON_NAMES = {
    "APIConnectionError": "api_connection_error",
    "APITimeoutError": "api_timeout",
    "ModelAPIError": "model_api_error",
}
REPORTS_DIR = Path(__file__).with_name("reports")
LEDGER_PATH = REPORTS_DIR / "spend-ledger.json"
ACCEPTANCE_BASELINE_PATH = REPORTS_DIR / "acceptance-baseline.json"
PREFIX_TOKEN_BUDGETS_PATH = REPORTS_DIR / "prefix-token-budgets.json"
SMOKE_CASE_NAMES = frozenset(
    {
        "student_capabilities",
        "my_profile",
        "my_achievements",
        "my_technology_stack",
        "technology_helpers",
        "student_read_my_goal",
        "my_attendance",
        "my_visits_month_choice",
        "student_upcoming_meetings",
        "set_my_technology_confirmation",
        "admin_points_change_confirmation",
        "admin_broadcast_confirmation",
        "admin_meeting_create_all",
        "admin_meeting_reschedule",
        "admin_meeting_cancel",
    }
)


class ExpectedToolCall(BaseModel):
    """A tool call and the subset of its arguments required by one synthetic case."""

    tool_name: str
    args: dict[str, JsonValue]


class EvalCase(BaseModel):
    """One synthetic actor turn and its expected observable behavior."""

    name: str
    question: str
    roles: frozenset[str]
    expected_tools: set[str]
    forbidden_tools: set[str] = Field(default_factory=set)
    security: bool = False
    expected_confirmation: bool = False
    expected_call_args: list[ExpectedToolCall] = Field(default_factory=list)
    required_phrases: list[str]
    forbidden_phrases: list[str]
    history: list[AgentMemoryMessage] = Field(default_factory=list)
    search_mode: Literal["unique", "ambiguous", "none"] = "unique"
    private_chat: bool = False
    is_forwarded: bool = False
    confirm_after_preview: bool = False


@dataclass(frozen=True)
class ObservedCall:
    """Transient model evidence used for metrics; never written to reports."""

    tool_name: str
    args: dict[str, JsonValue]


@dataclass(frozen=True)
class CaseRun:
    """Aggregate-safe metrics for one execution of a synthetic case."""

    case_name: str
    passed: bool
    tools_correct: bool
    confirmation_correct: bool
    phrases_correct: bool
    action_args_correct: int
    action_args_expected: int
    requests: int
    latency_s: float
    cost_rub: Decimal | None
    exact_cost_rub: Decimal
    reserved_cost_rub: Decimal
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    security_errors: dict[str, int]
    blocked_attempts: dict[str, int]
    executed_after_confirmation: int
    failure_reasons: tuple[str, ...] = ()
    request_failures: tuple[dict[str, JsonValue], ...] = ()
    connection_failure: bool = False
    connection_diagnostic: dict[str, JsonValue] | None = None
    finish_reason_length_responses: int = 0


@dataclass(frozen=True)
class ReportMetadata:
    """Non-sensitive metadata that identifies an evaluation report."""

    model: str
    repeat: int
    prefix_tokens: dict[str, dict[str, int | str]]
    forecast: Decimal
    ledger: SpendLedger
    aborted: bool
    abort_reason: str | None
    initial_cost_rub: Decimal | None = None
    budget_limit_rub: Decimal = STOP_BUDGET_RUB
    ledger_limit_rub: Decimal = MAX_BUDGET_RUB
    stop_limit_rub: Decimal = STOP_BUDGET_RUB
    evaluation_scope: Literal["full", "tool", "smoke", "cases"] = "full"
    selected_tool: str | None = None


class BudgetStopError(RuntimeError):
    """Stop before sending more paid requests when spend cannot be bounded."""


class SpendLedger:
    """Persist cumulative Polza cost only, without questions, outputs, or identities."""

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = path
        self.total_cost_rub = Decimal("0")
        self.unpriced_requests = 0
        self.request_count = 0
        self.global_limit_rub = MAX_BUDGET_RUB
        self.stop_limit_rub = STOP_BUDGET_RUB
        self.entries: list[dict[str, JsonValue]] = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.total_cost_rub = Decimal(data["total_cost_rub"])
            self.unpriced_requests = int(data["unpriced_requests"])
            self.request_count = int(data["request_count"])
            self.global_limit_rub = Decimal(data.get("global_limit_rub", format(MAX_BUDGET_RUB, "f")))
            self.stop_limit_rub = Decimal(data.get("stop_limit_rub", format(STOP_BUDGET_RUB, "f")))
            self.entries = data.get("entries", [])

    def record_cost(self, model: str, cost_rub: Decimal, *, estimated: bool = False, source: str = "cost_rub") -> None:
        self.total_cost_rub += cost_rub
        self.request_count += 1
        self.entries.append({"cost_rub": format(cost_rub, "f"), "estimated": estimated, "source": source})
        self._write(model)

    def _write(self, model: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        payload = {
            "model": model,
            "total_cost_rub": format(self.total_cost_rub, "f"),
            "global_limit_rub": format(self.global_limit_rub, "f"),
            "stop_limit_rub": format(self.stop_limit_rub, "f"),
            "request_count": self.request_count,
            "unpriced_requests": self.unpriced_requests,
            "estimated_records": sum(entry.get("estimated") is True for entry in self.entries),
            "exact_records": sum(entry.get("estimated") is False for entry in self.entries),
            "entries": self.entries,
        }
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(self.path)


class ObservedPolzaModel(WrapperModel):
    """Capture usage and reserve budget using exact charges or marked upper estimates."""

    def __init__(  # noqa: PLR0913
        self,
        wrapped: Model,
        model_name: str,
        ledger: SpendLedger,
        budget: Decimal,
        tariff: ModelTariff | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        super().__init__(wrapped)
        self.polza_model_name = model_name
        self.ledger = ledger
        self.budget = budget
        self.tariff = tariff or ModelTariff()
        self.response_tariff = self.tariff
        self.timeout_seconds = timeout_seconds
        self.request_count = 0
        self.responses: list[ModelResponse] = []
        self.request_latencies_s: list[float] = []
        self.run_cost_rub = Decimal("0")
        self.run_exact_cost_rub = Decimal("0")
        self.run_reserved_cost_rub = Decimal("0")
        self.budget_exceeded = False
        self.stop_reason: str | None = None
        self.failures: list[dict[str, JsonValue]] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        estimated_input_tokens = _estimate_request_input_tokens(messages, model_request_parameters)
        if estimated_input_tokens > MAX_ESTIMATED_INPUT_TOKENS_PER_REQUEST:
            self.stop_reason = "estimated_input_limit"
            raise BudgetStopError("The request exceeded the preflight input token limit")
        output_limit = model_settings.get("max_tokens") if model_settings is not None else None
        if not isinstance(output_limit, int) or output_limit > MAX_OUTPUT_TOKENS_PER_REQUEST:
            self.stop_reason = "output_limit_not_bounded"
            raise BudgetStopError("The request output limit is not bounded by the budget forecast")
        maximum_request_cost = _estimated_request_cost(estimated_input_tokens, output_limit, self.tariff)
        if self.ledger.total_cost_rub + maximum_request_cost > self.budget:
            self.budget_exceeded = True
            self.stop_reason = "remaining_budget_below_single_request_reserve"
            raise BudgetStopError("The remaining budget cannot cover the maximum cost of one request")

        self.request_count += 1
        started_at = time.perf_counter()
        self.response_tariff = self.tariff
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except BaseException as error:
            elapsed_s = time.perf_counter() - started_at
            if isinstance(error, ModelHTTPError):
                cost = Decimal("0")
                self.ledger.record_cost(
                    self.polza_model_name,
                    cost,
                    source="http_error_no_charge_polza_docs",
                )
            else:
                cost = _estimated_request_cost(estimated_input_tokens, output_limit, self.response_tariff)
                self.ledger.record_cost(
                    self.polza_model_name,
                    cost,
                    estimated=True,
                    source="request_error_upper_bound",
                )
            self.run_cost_rub += cost
            if isinstance(error, ModelHTTPError):
                self.run_exact_cost_rub += cost
            else:
                self.run_reserved_cost_rub += cost
            diagnostic = _exception_diagnostic(error)
            diagnostic["usage_received"] = False
            diagnostic["elapsed_s"] = round(elapsed_s, 3)
            if self.timeout_seconds is not None:
                diagnostic["agent_timeout_s"] = self.timeout_seconds
                diagnostic["http_client_timeout_s"] = self.timeout_seconds
                if isinstance(error, asyncio.CancelledError) and elapsed_s >= self.timeout_seconds - 0.5:
                    diagnostic["cause"] = "agent_timeout_s"
            if isinstance(error, ModelHTTPError):
                diagnostic["http_status"] = error.status_code
                body_text = str(error.body).casefold()
                diagnostic["recognized_error_terms"] = [
                    term
                    for term in (
                        "reasoning_content",
                        "parallel_tool_calls",
                        "max_tokens",
                        "model",
                        "provider",
                        "provider_name",
                        "rate",
                    )
                    if term in body_text
                ]
            self.failures.append(diagnostic)
            raise
        finally:
            self.request_latencies_s.append(time.perf_counter() - started_at)

        self._record_response(response, estimated_input_tokens, output_limit)
        return response

    def _record_response(self, response: ModelResponse, estimated_input_tokens: int, output_limit: int) -> None:
        """Prefer exact billing; otherwise price usage or the bounded request."""
        self.responses.append(response)
        cost_rub = _response_cost(response)
        estimated = cost_rub is None
        cost_source = "cost_rub"
        if estimated:
            if (
                response.usage.input_tokens
                or response.usage.output_tokens
                or (response.provider_details or {}).get("polza_usage")
            ):
                cost_rub = _estimated_response_cost(response, self.response_tariff)
                cost_source = "usage_tokens"
            else:
                cost_rub = _estimated_request_cost(estimated_input_tokens, output_limit, self.response_tariff)
                cost_source = "missing_usage_upper_bound"
        self.ledger.record_cost(
            self.polza_model_name,
            cost_rub,
            estimated=estimated,
            source=cost_source,
        )
        self.run_cost_rub += cost_rub
        if estimated:
            self.run_reserved_cost_rub += cost_rub
        else:
            self.run_exact_cost_rub += cost_rub
        if self.ledger.unpriced_requests:
            raise BudgetStopError("A previous Polza request has an unknown cost")
        if self.ledger.total_cost_rub > self.budget:
            self.budget_exceeded = True
            self.stop_reason = "cumulative_budget_exceeded"
            raise BudgetStopError("The cumulative Polza budget was exceeded")


class EvalModelPort(AgentModelPort):
    """Return the observed live model through the application model port."""

    def __init__(self, model: Model) -> None:
        self.model = model

    def get_model(self) -> Model:
        return self.model


class EvalMemory(AgentMemoryPort):
    """Provide case-local synthetic history and never persist outside this run."""

    def __init__(self, history: list[AgentMemoryMessage]) -> None:
        self.history = list(history)

    async def load(self, conversation: AgentConversation) -> list[AgentMemoryMessage]:
        del conversation
        return list(self.history)

    async def append(self, conversation: AgentConversation, question: str, answer: str) -> None:
        del conversation
        self.history.extend(
            (AgentMemoryMessage(role="user", text=question), AgentMemoryMessage(role="assistant", text=answer))
        )

    async def mark_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> None:
        del chat_id, topic_id, message_id

    async def is_agent_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> bool:
        del chat_id, topic_id, message_id
        return False


class EvalUsers(UserService):
    """Return only the synthetic actor; no repository or database is available."""

    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    async def find_user_by_telegram_id(self, tg_id: int) -> UserReadDTO | None:
        if tg_id != ACTOR_TELEGRAM_ID:
            return None
        return _synthetic_user(tg_id)

    async def find_all_user_roles(self, user_id: int) -> set[str] | None:
        return set(self.roles) if user_id == ACTOR_TELEGRAM_ID else None


async def run_evals() -> int:  # noqa: C901, PLR0915
    """Run paid synthetic evaluations from the opt-in CLI and save aggregate metrics."""
    args = _parse_args()
    cases = _load_cases(args.case, tool_name=args.tool, smoke=args.smoke)
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={"agent_enabled": True, "agent_model": args.model, "broadcast_allowed_roles": frozenset({"Admin"})}
    )
    ledger = SpendLedger()
    catalog = ProviderCatalog.load(REPORTS_DIR / "provider-catalog.json")
    _validate_ledger_budget(ledger)
    initial_cost_rub = ledger.total_cost_rub
    run_budget = _run_budget(
        getattr(args, "max_run_cost_rub", None),
        initial_cost_rub,
        ledger.stop_limit_rub,
        ledger.global_limit_rub,
    )
    prefix_tokens = _role_prefix_token_counts()
    forecast = _forecast_max_batch_cost(len(cases), args.repeat, settings.agent_max_steps, catalog.tariff())
    evaluation_scope: Literal["full", "tool", "smoke", "cases"] = (
        "tool" if args.tool else "smoke" if args.smoke else "cases" if args.case else "full"
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    runs: list[CaseRun] = []
    aborted = False
    abort_reason: str | None = None
    http_metadata: list[dict[str, JsonValue]] = []
    observed_model: ObservedPolzaModel | None = None
    report_path = args.report or REPORTS_DIR / f"{run_id}.json"

    def save_report() -> dict[str, JsonValue]:
        return _write_eval_report(
            report_path,
            ReportMetadata(
                args.model,
                args.repeat,
                prefix_tokens,
                forecast,
                ledger,
                aborted,
                abort_reason,
                initial_cost_rub,
                run_budget,
                ledger.global_limit_rub,
                ledger.stop_limit_rub,
                evaluation_scope,
                args.tool,
            ),
            runs,
            http_metadata,
            catalog,
        )

    async def capture_metadata(response: httpx2.Response) -> None:
        await _capture_eval_response(response, catalog, observed_model, http_metadata)

    preflight_pressure = _toolset_pressure_result({"fixed_prefix_tokens": prefix_tokens}, evaluation_scope="full")
    if preflight_pressure["status"] == "STOP":
        aborted, abort_reason = True, "toolset_prefix_budget_approaching"
        report = save_report()
        print(f"STOP: {abort_reason}; платные запросы не отправлялись.")
        _print_toolset_pressure(report)
        return 2

    async with httpx2.AsyncClient(
        timeout=settings.agent_timeout_s, event_hooks={"response": [capture_metadata]}
    ) as client:
        base_model = _build_eval_model(settings, client)
        try:
            for repetition in range(1, args.repeat + 1):
                for case in cases:
                    observed_model = ObservedPolzaModel(
                        base_model,
                        args.model,
                        ledger,
                        run_budget,
                        catalog.tariff(),
                        timeout_seconds=settings.agent_timeout_s,
                    )
                    try:
                        case_run = await _run_case(case, settings, observed_model)
                    except BudgetStopError:
                        aborted, abort_reason = True, observed_model.stop_reason or "budget_reserve"
                        break
                    runs.append(case_run)
                    _print_case_progress(case_run, repetition, args.repeat, observed_model)
                    abort_reason = _model_stop_reason(observed_model)
                    aborted = abort_reason is not None
                    save_report()
                    if aborted:
                        break
                if aborted:
                    break
        except asyncio.CancelledError:
            aborted, abort_reason = True, "interrupted"
            raise
        finally:
            report = save_report()

    print(
        f"Отчёт: {report_path}; расход этого запуска: {report['budget']['run_cost_rub']} ₽; "
        f"накопительно: {report['budget']['cumulative_cost_rub']} ₽"
    )
    _print_acceptance(report)
    _print_toolset_pressure(report)
    if aborted:
        print(f"STOP: {abort_reason or 'не удалось безопасно оценить стоимость'}; новые запросы не отправлялись.")
        return 2
    pressure = report.get("toolset_pressure")
    if isinstance(pressure, dict) and pressure.get("status") == "STOP":
        return 1
    return 0 if all(run.passed or run.connection_failure for run in runs) else 1


async def _capture_eval_response(
    response: httpx2.Response,
    catalog: ProviderCatalog,
    model: ObservedPolzaModel | None,
    http_metadata: list[dict[str, JsonValue]],
) -> None:
    """Keep safe HTTP metadata and update only the serving-provider tariff."""
    await response.aread()
    http_metadata.append(_safe_http_metadata(response, {provider.name for provider in catalog.providers}))
    if model is not None:
        model.response_tariff = _response_provider_tariff(response, catalog)


def _print_case_progress(case_run: CaseRun, repetition: int, repeat: int, model: ObservedPolzaModel) -> None:
    """Print synthetic case labels and fixed error diagnostics, never conversations."""
    print(f"{case_run.case_name} [{repetition}/{repeat}]: {'PASS' if case_run.passed else 'FAIL'}")
    for reason in case_run.failure_reasons:
        print(f"  reason: {reason}")
    for failure in model.failures:
        print(json.dumps(failure, ensure_ascii=False))


def _write_eval_report(
    path: Path,
    metadata: ReportMetadata,
    runs: list[CaseRun],
    http_metadata: list[dict[str, JsonValue]],
    catalog: ProviderCatalog,
) -> dict[str, JsonValue]:
    """Atomically save completed-case metrics and all recorded request liabilities."""
    report = _build_report(metadata, runs)
    report["evaluation_scope"] = metadata.evaluation_scope
    report["selected_tool"] = metadata.selected_tool
    report["http_response_metadata"] = http_metadata
    report["catalog_tariffs"] = catalog.model_dump(mode="json")
    report["tariffs_checked_at"] = "2026-10-13"
    report["acceptance"] = _acceptance_result(report, metadata.evaluation_scope)
    report["toolset_pressure"] = _toolset_pressure_result(report, metadata.evaluation_scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)
    return report


def _acceptance_result(report: dict[str, JsonValue], evaluation_scope: str) -> dict[str, JsonValue]:
    """Compare full repeat-three runs with the saved B3 quality, cost, cache, and security limits."""
    baseline = json.loads(ACCEPTANCE_BASELINE_PATH.read_text(encoding="utf-8"))
    reasons: list[str] = []
    if evaluation_scope != "full" or report.get("repetitions_requested") != REQUIRED_ACCEPTANCE_REPETITIONS:
        return {
            "status": "НЕ ПРИНЯТО",
            "reasons": ["Критерии B3 проверяются только полным набором ×3."],
            "scope": evaluation_scope,
        }

    report_cases_value = report.get("cases")
    report_cases = report_cases_value if isinstance(report_cases_value, list) else []
    cases_by_name = {
        case.get("name"): case for case in report_cases if isinstance(case, dict) and isinstance(case.get("name"), str)
    }
    legacy_names = baseline["legacy_case_names"]
    expected_legacy_runs = baseline["legacy_case_count"] * 3
    legacy_case_rows = [cases_by_name.get(name) for name in legacy_names]
    legacy_rows = [case for case in legacy_case_rows if isinstance(case, dict)]
    legacy_runs = sum(_integer_metric(case, "runs") for case in legacy_rows)
    legacy_quality_runs = sum(_integer_metric(case, "quality_evaluable_runs") for case in legacy_rows)
    legacy_correct_tools = sum(_integer_metric(case, "tools_correct") for case in legacy_rows)
    old_quality_pct = _percentage(legacy_correct_tools, legacy_quality_runs)
    if legacy_runs < expected_legacy_runs:
        reasons.append(f"Старые кейсы выполнены {legacy_runs}/{expected_legacy_runs} раз.")
    if old_quality_pct is None or old_quality_pct < baseline["minimum_legacy_correct_tools_pct"]:
        reasons.append(
            f"Точность инструментов по старым 39 кейсам {old_quality_pct}% ниже "
            f"{baseline['minimum_legacy_correct_tools_pct']}%."
        )

    metrics_value = report.get("metrics")
    metrics = metrics_value if isinstance(metrics_value, dict) else {}
    budget_value = metrics.get("exact_cost_per_question_rub")
    exact_cost = _numeric_metric(budget_value, "mean") if isinstance(budget_value, dict) else None
    if exact_cost is None or exact_cost > float(baseline["maximum_exact_cost_per_question_rub"]):
        reasons.append(
            f"Точная цена на вопрос {exact_cost} ₽ превышает предел "
            f"{baseline['maximum_exact_cost_per_question_rub']} ₽."
        )
    cache_share = _numeric_metric(metrics, "cache_share_pct")
    if cache_share is None or cache_share < baseline["minimum_cache_share_pct"]:
        reasons.append(f"Доля кэша {cache_share}% ниже {baseline['minimum_cache_share_pct']}%.")
    usage_value = metrics.get("usage_totals")
    reasoning_tokens = _integer_metric(usage_value, "reasoning_tokens") if isinstance(usage_value, dict) else 0
    if reasoning_tokens != baseline["maximum_reasoning_tokens"]:
        reasons.append(f"Обнаружено reasoning-токенов: {reasoning_tokens}.")
    security_value = metrics.get("security_errors")
    security_errors = (
        {str(name): int(value) for name, value in security_value.items() if isinstance(value, int)}
        if isinstance(security_value, dict)
        else {}
    )
    if any(security_errors.values()):
        reasons.append(f"Ошибки безопасности: {security_errors}.")
    question_runs = _integer_metric(report, "question_runs")
    connection_failures = _integer_metric(metrics, "connection_failure_question_runs")
    connection_failure_pct = _percentage(connection_failures, question_runs)
    if connection_failure_pct is None or connection_failure_pct > baseline["maximum_connection_failure_pct"]:
        reasons.append(f"Сбои связи составили {connection_failure_pct}% вопросов.")

    accepted = not reasons
    if accepted:
        reasons.append("Все критерии B3 выполнены: качество, точная стоимость, кэш, reasoning и безопасность.")
    return {
        "status": "ПРИНЯТО" if accepted else "НЕ ПРИНЯТО",
        "reasons": reasons,
        "scope": evaluation_scope,
        "legacy_correct_tools_pct": old_quality_pct,
        "exact_cost_per_question_rub": exact_cost,
        "cache_share_pct": cache_share,
        "reasoning_tokens": reasoning_tokens,
        "connection_failure_pct": connection_failure_pct,
        "security_errors": security_errors,
        "baseline": {
            "legacy_correct_tools_pct": baseline["baseline_correct_tools_pct"],
            "minimum_legacy_correct_tools_pct": baseline["minimum_legacy_correct_tools_pct"],
            "baseline_exact_cost_per_question_rub": baseline["baseline_exact_cost_per_question_rub"],
            "maximum_exact_cost_per_question_rub": baseline["maximum_exact_cost_per_question_rub"],
            "baseline_cache_share_pct": baseline["baseline_cache_share_pct"],
            "minimum_cache_share_pct": baseline["minimum_cache_share_pct"],
        },
    }


def _toolset_pressure_result(report: dict[str, JsonValue], evaluation_scope: str) -> dict[str, JsonValue]:
    """Stop evals when role prefixes approach their ceiling or smoke selection regresses."""
    baseline = json.loads(ACCEPTANCE_BASELINE_PATH.read_text(encoding="utf-8"))
    budgets = json.loads(PREFIX_TOKEN_BUDGETS_PATH.read_text(encoding="utf-8"))["roles"]
    prefix_value = report.get("fixed_prefix_tokens")
    prefixes = prefix_value if isinstance(prefix_value, dict) else {}
    utilization: dict[str, float | None] = {}
    signals: list[str] = []

    for role, budget in budgets.items():
        role_prefix = prefixes.get(role)
        token_count = role_prefix.get("combined_tokens") if isinstance(role_prefix, dict) else None
        if not isinstance(budget, int) or not isinstance(token_count, int):
            utilization[role] = None
            continue
        share = round(token_count * PERCENTAGE_MULTIPLIER / budget, 2)
        utilization[role] = share
        if share >= PREFIX_PRESSURE_PCT:
            signals.append(f"Префикс роли {role} занимает {share}% лимита ({PREFIX_PRESSURE_PCT}% — порог STOP).")

    if evaluation_scope == "smoke":
        metrics_value = report.get("metrics")
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        accuracy = _numeric_metric(metrics, "correct_tools_pct")
        minimum_accuracy = float(baseline["smoke_minimum_correct_tools_pct"])
        if accuracy is None or accuracy < minimum_accuracy:
            signals.append(
                f"Точность выбора инструмента на smoke {accuracy}% ниже порога {minimum_accuracy}% "
                "(минимум по эталону старых 39 кейсов)."
            )

    return {
        "status": "STOP" if signals else "OK",
        "signals": signals,
        "prefix_utilization_pct": utilization,
        "prefix_stop_threshold_pct": PREFIX_PRESSURE_PCT,
        "smoke_minimum_correct_tools_pct": baseline["smoke_minimum_correct_tools_pct"],
        "suggestion": (
            "Остановить добавление инструментов и предложить владельцу объединить инструменты "
            "или выдавать их по контексту."
            if signals
            else None
        ),
    }


def _integer_metric(metrics: object, name: str) -> int:
    if not isinstance(metrics, dict):
        return 0
    value = metrics.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _numeric_metric(metrics: object, name: str) -> float | None:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(name)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _print_acceptance(report: dict[str, JsonValue]) -> None:
    """Print the saved B3 acceptance result and its concrete limiting conditions."""
    acceptance_value = report.get("acceptance")
    if not isinstance(acceptance_value, dict):
        print("НЕ ПРИНЯТО: в отчёте нет результата сравнения с B3.")
        return
    print(f"{acceptance_value.get('status', 'НЕ ПРИНЯТО')} по критериям B3.")
    reasons = acceptance_value.get("reasons", [])
    if isinstance(reasons, list):
        for reason in reasons:
            if isinstance(reason, str):
                print(f"  {reason}")


def _print_toolset_pressure(report: dict[str, JsonValue]) -> None:
    """Show the stop signal that protects smoke quality and fixed-prefix headroom."""
    pressure = report.get("toolset_pressure")
    if not isinstance(pressure, dict):
        return
    signals = pressure.get("signals", [])
    if pressure.get("status") != "STOP" or not isinstance(signals, list):
        print("Нагрузка на реестр инструментов в пределах зафиксированных порогов.")
        return
    print("STOP: ухудшилась точность smoke или префикс приблизился к бюджету.")
    for signal in signals:
        if isinstance(signal, str):
            print(f"  {signal}")
    suggestion = pressure.get("suggestion")
    if isinstance(suggestion, str):
        print(f"  Предложение владельцу: {suggestion}")


def _model_stop_reason(model: ObservedPolzaModel) -> str | None:
    """Stop a paid batch at its first HTTP 429; never retry 4xx automatically."""
    if any(failure.get("http_status") == HTTPStatus.TOO_MANY_REQUESTS for failure in model.failures):
        return "model_http_429"
    return model.stop_reason


async def _run_case(case: EvalCase, settings: AppSettings, model: ObservedPolzaModel) -> CaseRun:
    """Run one case through the real service policy with local synthetic adapters."""
    reads = EvalReads(search_mode=case.search_mode)
    users = EvalUsers(case.roles)
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    action_executors = _registered_action_executors(registry)
    actions = _synthetic_actions(action_executors)
    runtime = AgentRuntime()
    service = AgentService(
        users=users,
        model=EvalModelPort(model),
        memory=EvalMemory(case.history),
        runtime=runtime,
        registry=registry,
        settings=settings,
        reads=reads,
        actions=actions,
    )
    conversation = (
        AgentConversation(chat_id=1, topic_id=None, telegram_id=1)
        if case.private_chat
        else AgentConversation(chat_id=-100123, topic_id=7, telegram_id=1)
    )
    started_at = time.perf_counter()
    try:
        async with asyncio.timeout(settings.agent_timeout_s + 5):
            reply = await service.answer(conversation, case.question, is_forwarded=case.is_forwarded)
    except Exception:
        reply = AgentReply(status="unavailable")
    executions_before_confirmation = _execution_count(actions, action_executors)

    pending = runtime.find_pending(reply.confirmation_id) if reply.confirmation_id is not None else None
    if case.confirm_after_preview and reply.confirmation_id is not None:
        runtime.bind_message(reply.confirmation_id, 777)
        await service.confirm(reply.confirmation_id, conversation, 777, approved=True)
    executions_after_confirmation = _execution_count(actions, action_executors) - executions_before_confirmation
    latency_s = time.perf_counter() - started_at

    calls = [
        ObservedCall(tool_name=part.tool_name, args=part.args_as_dict())
        for response in model.responses
        for part in response.parts
        if isinstance(part, ToolCallPart)
    ]
    actual_tools = {call.tool_name for call in calls}
    tools_correct = actual_tools == case.expected_tools
    confirmation_correct = (reply.confirmation_id is not None) == case.expected_confirmation
    reply_text = reply.text.casefold()
    phrases_correct = all(phrase.casefold() in reply_text for phrase in case.required_phrases) and not any(
        phrase.casefold() in reply_text for phrase in case.forbidden_phrases
    )
    action_expected = [expected for expected in case.expected_call_args if expected.tool_name in action_executors]
    action_args_correct = sum(_has_matching_call(expected, calls) for expected in action_expected)
    all_args_correct = all(_has_matching_call(expected, calls) for expected in case.expected_call_args)
    status_correct = reply.status.value == "success"
    forbidden_tool_calls = actual_tools.intersection(case.forbidden_tools)
    passed = (
        tools_correct
        and not forbidden_tool_calls
        and confirmation_correct
        and phrases_correct
        and all_args_correct
        and status_correct
    )
    connection_failure = any(
        failure.get("error_type") == "ModelAPIError"
        and failure.get("usage_received") is False
        and "http_status" not in failure
        for failure in model.failures
    )
    connection_diagnostic = next(
        (
            failure
            for failure in reversed(model.failures)
            if failure.get("error_type") == "ModelAPIError"
            and failure.get("usage_received") is False
            and "http_status" not in failure
        ),
        None,
    )
    failure_reasons = (
        ("connection_failure=ModelAPIError_without_usage",)
        if connection_failure
        else _failure_reasons(
            case,
            actual_tools,
            confirmation_correct=confirmation_correct,
            reply_text=reply_text,
            all_args_correct=all_args_correct,
            calls=calls,
            status=reply.status.value,
        )
    )
    if forbidden_tool_calls:
        failure_reasons += (f"forbidden tool calls={sorted(forbidden_tool_calls)}",)

    blocked_attempts, security_errors = _security_counts(
        case,
        calls,
        pending,
        reads.allowed_search_recipient_ids,
        executions_before_confirmation,
        action_executors,
        registry,
    )
    if case.confirm_after_preview and executions_after_confirmation > 1:
        security_errors["multiple_actions_after_one_confirmation"] += 1

    usage: AgentUsageSummary = summarize_run_usage(RunUsage(requests=len(model.responses)), model.responses)
    cost = model.run_cost_rub
    return CaseRun(
        case_name=case.name,
        passed=passed,
        tools_correct=tools_correct,
        confirmation_correct=confirmation_correct,
        phrases_correct=phrases_correct,
        action_args_correct=action_args_correct,
        action_args_expected=len(action_expected),
        requests=model.request_count,
        latency_s=latency_s,
        cost_rub=cost,
        exact_cost_rub=model.run_exact_cost_rub,
        reserved_cost_rub=model.run_reserved_cost_rub,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        security_errors=security_errors,
        blocked_attempts=blocked_attempts,
        executed_after_confirmation=executions_after_confirmation,
        failure_reasons=failure_reasons,
        request_failures=tuple(model.failures),
        connection_failure=connection_failure,
        connection_diagnostic=connection_diagnostic,
        finish_reason_length_responses=sum(response.finish_reason == "length" for response in model.responses),
    )


def _failure_reasons(  # noqa: PLR0913
    case: EvalCase,
    actual_tools: set[str],
    *,
    confirmation_correct: bool,
    reply_text: str,
    all_args_correct: bool,
    calls: list[ObservedCall],
    status: str,
) -> tuple[str, ...]:
    """Describe why a synthetic case failed without storing the model's reply text."""
    reasons: list[str] = []
    if actual_tools != case.expected_tools:
        reasons.append(f"tools expected={sorted(case.expected_tools)} actual={sorted(actual_tools)}")
    if not confirmation_correct:
        reasons.append(f"confirmation expected={case.expected_confirmation}")
    missing = [phrase for phrase in case.required_phrases if phrase.casefold() not in reply_text]
    forbidden = [phrase for phrase in case.forbidden_phrases if phrase.casefold() in reply_text]
    if missing:
        reasons.append(f"phrases missing={missing}")
    if forbidden:
        reasons.append(f"phrases forbidden_present={forbidden}")
    if not all_args_correct:
        wrong = [expected.tool_name for expected in case.expected_call_args if not _has_matching_call(expected, calls)]
        reasons.append(f"args mismatch tools={wrong}")
    if status != "success":
        reasons.append(f"status={status}")
    return tuple(reasons)


def _synthetic_actions(action_executors: dict[str, str]) -> Mock:
    """Create mock-only action ports; no action reaches a real service or Telegram."""
    actions = Mock(spec=AgentActionsPort)

    async def find_user(telegram_id: int) -> UserReadDTO:
        if telegram_id not in {FIRST_SYNTHETIC_USER_ID, SECOND_SYNTHETIC_USER_ID}:
            raise UserNotFoundError(telegram_id=telegram_id)
        return _synthetic_user(telegram_id)

    actions.find_user.side_effect = find_user
    for method_name in action_executors.values():
        getattr(actions, method_name).return_value = "Синтетическое действие выполнено."
    for method_name in (
        "preview_meeting_creation",
        "preview_meeting_reschedule",
        "preview_meeting_cancellation",
    ):
        getattr(actions, method_name).return_value = "Синтетическое подтверждение встречи."
    return actions


def _registered_action_executors(registry: AgentToolRegistry) -> dict[str, str]:
    """Get synthetic action methods from the tool metadata instead of a parallel mapping."""
    action_executors: dict[str, str] = {}
    for entry in registry.entries:
        if entry.metadata.kind != "action":
            continue
        service_name, separator, method_name = entry.metadata.service.partition(".")
        if service_name != "AgentActionsPort" or not separator or not method_name:
            raise ValueError(f"Action tool {entry.metadata.name} must name its AgentActionsPort method")
        action_executors[entry.metadata.name] = method_name
    return action_executors


def _synthetic_user(telegram_id: int) -> UserReadDTO:
    """Build a non-personal DTO for the actor and the two fixture recipients."""
    name, username, user_id = {
        ACTOR_TELEGRAM_ID: ("Тест", None, ACTOR_TELEGRAM_ID),
        FIRST_SYNTHETIC_USER_ID: ("Иван", "test_student", 77),
        SECOND_SYNTHETIC_USER_ID: ("Анна", "anna_test", 88),
    }[telegram_id]
    return UserReadDTO(
        id=user_id,
        telegram_id=telegram_id,
        first_name=name,
        last_name="Иванов" if telegram_id != SECOND_SYNTHETIC_USER_ID else "Петрова",
        patronymic=None,
        telegram_username=username,
        join_date=date(2026, 1, 1),
        academic_points=Points(value=75, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=12, point_type=PointsTypeEnum.REPUTATION),
    )


def _security_counts(  # noqa: PLR0913
    case: EvalCase,
    calls: list[ObservedCall],
    pending: PendingAgentRequest | None,
    searched_recipient_ids: set[int],
    executions_before_confirmation: int,
    action_executors: dict[str, str],
    registry: AgentToolRegistry,
) -> tuple[dict[str, int], dict[str, int]]:
    """Count actual security violations separately from tool attempts blocked by code."""
    allowed_action_names = set(registry.for_roles(case.roles).policies).intersection(action_executors)
    attempted_forbidden = sum(
        call.tool_name in action_executors and call.tool_name not in allowed_action_names for call in calls
    )
    pending_calls = pending.requests.approvals if pending is not None else []
    outside_rights = sum(call.tool_name not in allowed_action_names for call in pending_calls)
    allowed_recipients = set() if case.is_forwarded else _explicit_recipient_ids(case.question) | searched_recipient_ids
    wrong_attempts = 0
    wrong_pending = 0
    for call in calls:
        telegram_id = call.args.get("telegram_id")
        if (
            call.tool_name in action_executors
            and isinstance(telegram_id, int)
            and telegram_id not in allowed_recipients
        ):
            wrong_attempts += 1
    for call in pending_calls:
        arguments = call.args_as_dict()
        telegram_id = arguments.get("telegram_id")
        if isinstance(telegram_id, int) and telegram_id not in allowed_recipients:
            wrong_pending += 1
    security_errors = {
        "action_outside_rights": outside_rights,
        "wrong_recipient": wrong_pending,
        "without_confirmation": executions_before_confirmation,
        "forbidden_tool_call": len(set(call.tool_name for call in calls).intersection(case.forbidden_tools)),
    }
    blocked_attempts = {
        "action_outside_rights": attempted_forbidden,
        "wrong_recipient": wrong_attempts,
    }
    return blocked_attempts, security_errors


def _explicit_recipient_ids(question: str) -> set[int]:
    label = r"(?:telegram\s*id|id\s*telegram|телеграм(?:а)?\s*ид)"
    return {int(match) for match in re.findall(rf"{label}\s*(?:[:=#№-]\s*)?(\d+)", question, re.IGNORECASE)}


def _has_matching_call(expected: ExpectedToolCall, calls: list[ObservedCall]) -> bool:
    return any(
        call.tool_name == expected.tool_name and _contains_expected_values(call.args, expected.args) for call in calls
    )


def _contains_expected_values(actual: JsonValue, expected: JsonValue) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_expected_values(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _contains_expected_values(actual_item, expected_item)
                for actual_item, expected_item in zip(actual, expected, strict=True)
            )
        )
    return actual == expected


def _execution_count(actions: Mock, action_executors: dict[str, str]) -> int:
    return sum(getattr(actions, method_name).await_count for method_name in action_executors.values())


def _response_cost(response: ModelResponse) -> Decimal | None:
    details = response.provider_details or {}
    usage = details.get("polza_usage")
    if not isinstance(usage, Mapping):
        return None
    raw_cost = usage.get("cost_rub")
    if isinstance(raw_cost, bool) or not isinstance(raw_cost, int | float | str | Decimal):
        return None
    try:
        cost = Decimal(str(raw_cost))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def _estimate_request_input_tokens(
    messages: list[ModelMessage], model_request_parameters: ModelRequestParameters
) -> int:
    """Estimate serialized messages and tool schemas with a 2x request-budget margin."""
    payload = {
        "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json", exclude_none=True),
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters_json_schema,
                },
            }
            for tool in model_request_parameters.function_tools
        ],
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(tiktoken.get_encoding("cl100k_base").encode(serialized_payload)) + REQUEST_TOKEN_OVERHEAD


def _validate_ledger_budget(ledger: SpendLedger) -> None:
    """Require recorded liabilities to leave room for the next bounded request."""
    if ledger.unpriced_requests:
        raise BudgetStopError("The spend ledger contains an earlier request with unknown cost")
    if ledger.total_cost_rub > ledger.stop_limit_rub:
        raise BudgetStopError(f"Recorded spend has reached the {ledger.stop_limit_rub} ₽ stop threshold")
    if ledger.total_cost_rub + _maximum_request_cost(MAX_OUTPUT_TOKENS_PER_REQUEST) > ledger.stop_limit_rub:
        raise BudgetStopError(
            f"Recorded spend leaves less than the maximum cost of one request under the "
            f"{ledger.stop_limit_rub} ₽ stop threshold"
        )
    if ledger.stop_limit_rub > ledger.global_limit_rub:
        raise BudgetStopError("The configured STOP threshold is above the global ledger limit")


def _response_provider_tariff(response: httpx2.Response, catalog: ProviderCatalog) -> ModelTariff:
    """Use serving-provider prices only when the response explicitly identifies it."""
    try:
        body = response.json()
    except ValueError:
        return catalog.tariff()
    provider_name = body.get("provider") if isinstance(body, dict) else None
    return catalog.tariff(provider_name if isinstance(provider_name, str) else None)


def _exception_diagnostic(error: BaseException) -> dict[str, JsonValue]:
    """Keep exception types and fixed cause categories without serializing messages."""
    exception_chain: list[dict[str, JsonValue]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        exception_type = type(current)
        exception_chain.append(
            {
                "type": f"{exception_type.__module__}.{exception_type.__qualname__}",
                "reason": _exception_reason(current),
            }
        )
        current = current.__cause__ or current.__context__

    cause = next(
        (
            entry["reason"]
            for entry in reversed(exception_chain[1:])
            if entry["reason"] not in {"other", "api_connection_error"}
        ),
        None,
    )
    if cause is None:
        cause = exception_chain[-1]["reason"] if len(exception_chain) > 1 else "cause_unavailable"
    return {
        "error_type": type(error).__name__,
        "cause": cause,
        "exception_chain": exception_chain,
    }


def _exception_reason(error: BaseException) -> str:
    """Map transport exceptions to fixed categories that cannot contain private text."""
    for exception_type, reason in _EXCEPTION_REASONS:
        if isinstance(error, exception_type):
            return reason
    exception_name = type(error).__name__
    if exception_name in _EXCEPTION_REASON_NAMES:
        return _EXCEPTION_REASON_NAMES[exception_name]
    return "other"


def _safe_http_metadata(
    response: httpx2.Response, known_provider_names: set[str] | frozenset[str] = frozenset()
) -> dict[str, JsonValue]:
    """Keep only field names, numeric billing data and fixed error categories."""
    metadata: dict[str, JsonValue] = {"status": response.status_code}
    request_body = json.loads(response.request.content)
    metadata["request_fields"] = sorted(request_body)
    metadata["message_roles"] = [message.get("role") for message in request_body.get("messages", [])]
    metadata["tool_strict_flags"] = [tool.get("function", {}).get("strict") for tool in request_body.get("tools", [])]
    metadata["tool_choice"] = request_body.get("tool_choice")
    provider_routing = _safe_provider_routing(request_body, known_provider_names)
    if provider_routing is not None:
        metadata["provider_routing"] = provider_routing
    try:
        body = response.json()
    except ValueError:
        metadata["json_body"] = False
        return metadata
    if not isinstance(body, dict):
        return metadata
    metadata["fields"] = sorted(body)
    serving_provider = body.get("provider")
    if isinstance(serving_provider, str) and serving_provider in known_provider_names:
        metadata["serving_provider"] = serving_provider
    usage = body.get("usage")
    if isinstance(usage, dict):
        metadata["usage_fields"] = sorted(usage)
        metadata["billing"] = _numeric_billing_fields(usage)
    error = body.get("error")
    if error is not None:
        metadata.update(_safe_response_error_metadata(error))
    return metadata


def _safe_provider_routing(
    request_body: dict[str, JsonValue], known_provider_names: set[str] | frozenset[str]
) -> dict[str, JsonValue] | None:
    """Keep only catalog IDs and booleans from the request's routing object."""
    provider_route = request_body.get("provider")
    if not isinstance(provider_route, dict):
        return None
    safe_route: dict[str, JsonValue] = {}
    for name in ("only", "order"):
        providers = provider_route.get(name)
        if isinstance(providers, list) and all(
            isinstance(provider, str) and provider in known_provider_names for provider in providers
        ):
            safe_route[name] = [provider for provider in providers if isinstance(provider, str)]
    for name in ("require_parameters", "allow_fallbacks"):
        value = provider_route.get(name)
        if isinstance(value, bool):
            safe_route[name] = value
    return safe_route or None


def _safe_response_error_metadata(error: JsonValue) -> dict[str, JsonValue]:
    """Reduce an HTTP error to fixed categories and field names, never its message."""
    metadata: dict[str, JsonValue] = {}
    if isinstance(error, str) and re.fullmatch(r"[A-Z_]{1,80}", error):
        metadata["error_code"] = error
    elif isinstance(error, dict):
        metadata["error_fields"] = sorted(error)
        code = error.get("code")
        if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", code):
            metadata["error_code"] = code
        details = error.get("metadata")
        if isinstance(details, dict):
            metadata["error_metadata_fields"] = sorted(details)
    error_text = str(error).casefold()
    metadata["error_categories"] = [
        term
        for term in (
            "upstream",
            "timeout",
            "unavailable",
            "unsupported",
            "model",
            "provider",
            "credit",
            "quota",
            "bad gateway",
            "empty",
            "response",
            "length",
            "content",
            "ошибка",
            "ответ",
            "пуст",
            "лимит",
        )
        if term in error_text
    ]
    return metadata


def _numeric_billing_fields(usage: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Preserve numeric usage and nested cost/cache details without arbitrary text."""
    result: dict[str, JsonValue] = {}
    for name, value in usage.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            result[name] = value
        elif name in {"cost", "cost_rub"} and isinstance(value, str):
            try:
                cost = Decimal(value)
            except InvalidOperation:
                continue
            if cost.is_finite() and cost >= 0:
                result[name] = format(cost, "f")
        elif isinstance(value, dict):
            result[name] = _numeric_billing_fields(value)
    return result


def _build_eval_model(settings: AppSettings, client: httpx2.AsyncClient) -> Model:
    """Disable hidden SDK retries so each HTTP attempt has a ledger entry."""
    model = PolzaAgentModel(settings, client).get_model()
    if isinstance(model, OpenAIChatModel):
        model.client.max_retries = 0
    return model


def _estimated_response_cost(response: ModelResponse, tariff: ModelTariff | None = None) -> Decimal:
    """Price reported tokens, treating cached input as ordinary input without a cache tariff."""
    usage = (response.provider_details or {}).get("polza_usage")
    if isinstance(usage, Mapping):
        input_tokens = usage.get("input_tokens", response.usage.input_tokens)
        output_tokens = usage.get("output_tokens", response.usage.output_tokens)
    else:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        raise BudgetStopError("Response token counters are invalid")
    cached_tokens = usage.get("cached_tokens", 0) if isinstance(usage, Mapping) else response.usage.cache_read_tokens
    if not isinstance(cached_tokens, int):
        cached_tokens = 0
    return (tariff or ModelTariff()).estimate(input_tokens, output_tokens, cached_tokens)


def _estimated_request_cost(
    estimated_input_tokens: int, output_limit: int, tariff: ModelTariff | None = None
) -> Decimal:
    """Price a failed request using twice its proxy input and its full generation limit."""
    return (tariff or ModelTariff()).estimate(estimated_input_tokens * 2, output_limit)


def _maximum_request_cost(output_tokens: int, tariff: ModelTariff | None = None) -> Decimal:
    """Reserve the maximum request liability before each paid API call."""
    return (tariff or ModelTariff()).estimate(MAX_INPUT_TOKENS_PER_REQUEST, output_tokens)


def _load_cases(
    case_names: list[str] | None,
    *,
    tool_name: str | None = None,
    smoke: bool = False,
) -> list[EvalCase]:
    cases = TypeAdapter(list[EvalCase]).validate_json(
        Path(__file__).with_name("cases.json").read_text(encoding="utf-8")
    )
    if case_names and (tool_name is not None or smoke):
        raise ValueError("--case cannot be combined with --tool or --smoke")
    if tool_name is not None and smoke:
        raise ValueError("--tool and --smoke cannot be combined")
    if not case_names and tool_name is None and not smoke and len(cases) < MINIMUM_CASE_COUNT:
        raise ValueError("The agent evaluation set must contain at least 30 cases")
    if case_names:
        return _select_named_cases(cases, case_names)
    if tool_name is not None:
        return _select_tool_cases(cases, tool_name)
    if smoke:
        return _select_smoke_cases(cases)
    return cases


def _select_named_cases(cases: list[EvalCase], case_names: list[str]) -> list[EvalCase]:
    """Return requested named cases and report missing names before any API call."""
    selected_names = set(case_names)
    selected = [case for case in cases if case.name in selected_names]
    missing = selected_names - {case.name for case in selected}
    if missing:
        raise ValueError(f"Unknown evaluation case: {', '.join(sorted(missing))}")
    return selected


def _select_tool_cases(cases: list[EvalCase], tool_name: str) -> list[EvalCase]:
    """Select all positive and explicitly forbidden calls for one registered tool."""
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    if tool_name not in {entry.metadata.name for entry in registry.entries}:
        raise ValueError(f"Unknown agent tool: {tool_name}")
    selected = [case for case in cases if tool_name in case.expected_tools or tool_name in case.forbidden_tools]
    if not selected:
        raise ValueError(f"No evaluation cases cover agent tool: {tool_name}")
    return selected


def _select_smoke_cases(cases: list[EvalCase]) -> list[EvalCase]:
    """Select named representative flows and every case marked as security-sensitive."""
    selected_names = SMOKE_CASE_NAMES | {case.name for case in cases if case.security}
    selected = [case for case in cases if case.name in selected_names]
    missing = SMOKE_CASE_NAMES - {case.name for case in selected}
    if missing:
        raise ValueError(f"Unknown smoke evaluation case: {', '.join(sorted(missing))}")
    return selected


def _role_prefix_token_counts() -> dict[str, dict[str, int | str]]:
    """Estimate the fixed instruction plus JSON tool schemas with a documented proxy tokenizer."""
    encoding = tiktoken.get_encoding("cl100k_base")
    instructions = build_agent_instructions()
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    counts: dict[str, dict[str, int | str]] = {}
    for role in ("Student", "Mentor", "Admin"):
        toolset = registry.for_roles(frozenset({role}))
        schema_payload = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.function_schema.json_schema,
                },
            }
            for tool in toolset.tools.values()
        ]
        schema_json = json.dumps(schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        combined_prefix = f"{instructions}\n{schema_json}"
        counts[role.casefold()] = {
            "instruction_tokens": len(encoding.encode(instructions)),
            "tool_schema_tokens": len(encoding.encode(schema_json)),
            "combined_tokens": len(encoding.encode(combined_prefix)),
        }
    return counts


def _forecast_max_batch_cost(
    case_count: int, repeat: int, request_limit: int, tariff: ModelTariff | None = None
) -> Decimal:
    """Forecast a bounded worst case from the harness request, input, and output limits."""
    return _maximum_request_cost(MAX_OUTPUT_TOKENS_PER_REQUEST, tariff) * case_count * repeat * request_limit


def _build_report(
    metadata: ReportMetadata,
    runs: list[CaseRun],
) -> dict[str, JsonValue]:
    quality_runs = [run for run in runs if not run.connection_failure]
    connection_failure_runs = [run for run in runs if run.connection_failure]
    latencies = [run.latency_s for run in runs]
    costs = [run.cost_rub for run in runs]
    priced_costs = [cost for cost in costs if cost is not None]
    all_costs_known = len(priced_costs) == len(costs)
    requests = sum(run.requests for run in runs)
    input_tokens = sum(run.input_tokens for run in runs)
    cached_tokens = sum(run.cached_tokens for run in runs)
    output_tokens = sum(run.output_tokens for run in runs)
    reasoning_tokens = sum(run.reasoning_tokens for run in runs)
    completed_cost = sum(priced_costs, start=Decimal("0")) if all_costs_known else None
    exact_cost_total = sum((run.exact_cost_rub for run in runs), start=Decimal("0"))
    reserved_cost_total = sum((run.reserved_cost_rub for run in runs), start=Decimal("0"))
    exact_cost_per_question = exact_cost_total / len(runs) if runs else None
    reserved_cost_per_question = reserved_cost_total / len(runs) if runs else None
    ledger_exact_total = sum(
        (Decimal(str(entry["cost_rub"])) for entry in metadata.ledger.entries if entry.get("estimated") is False),
        start=Decimal("0"),
    )
    ledger_reserved_total = sum(
        (Decimal(str(entry["cost_rub"])) for entry in metadata.ledger.entries if entry.get("estimated") is True),
        start=Decimal("0"),
    )
    run_cost = (
        metadata.ledger.total_cost_rub - metadata.initial_cost_rub
        if metadata.initial_cost_rub is not None
        else completed_cost
    )
    security_names = (
        "action_outside_rights",
        "wrong_recipient",
        "without_confirmation",
        "multiple_actions_after_one_confirmation",
        "forbidden_tool_call",
    )
    security_errors = {name: sum(run.security_errors.get(name, 0) for run in runs) for name in security_names}
    grouped: dict[str, list[CaseRun]] = defaultdict(list)
    for run in runs:
        grouped[run.case_name].append(run)
    cases = [
        {
            "name": name,
            "runs": len(case_runs),
            "quality_evaluable_runs": sum(not run.connection_failure for run in case_runs),
            "connection_failures": sum(run.connection_failure for run in case_runs),
            "passed": sum(run.passed for run in case_runs if not run.connection_failure),
            "tools_correct": sum(run.tools_correct for run in case_runs if not run.connection_failure),
            "confirmations_correct": sum(run.confirmation_correct for run in case_runs if not run.connection_failure),
            "phrases_correct": sum(run.phrases_correct for run in case_runs if not run.connection_failure),
            "action_args_correct": sum(run.action_args_correct for run in case_runs if not run.connection_failure),
            "action_args_expected": sum(run.action_args_expected for run in case_runs if not run.connection_failure),
            "finish_reason_length_responses": sum(run.finish_reason_length_responses for run in case_runs),
            "blocked_attempts": {
                "action_outside_rights": sum(run.blocked_attempts["action_outside_rights"] for run in case_runs),
                "wrong_recipient": sum(run.blocked_attempts["wrong_recipient"] for run in case_runs),
            },
            "actions_executed_after_confirmation": sum(run.executed_after_confirmation for run in case_runs),
            "failure_reasons": sorted({reason for run in case_runs for reason in run.failure_reasons}),
        }
        for name, case_runs in sorted(grouped.items())
    ]
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "model": metadata.model,
        "repetitions_requested": metadata.repeat,
        "question_runs": len(runs),
        "connection_failures": [
            {
                "case": run.case_name,
                **(
                    run.connection_diagnostic
                    or {"error_type": "ModelAPIError", "cause": "cause_unavailable", "usage_received": False}
                ),
            }
            for run in connection_failure_runs
        ],
        "model_request_failures": [
            {"case": run.case_name, **failure} for run in runs for failure in run.request_failures
        ],
        "aborted": metadata.aborted,
        "abort_reason": metadata.abort_reason,
        "tokenizer": "cl100k_base (proxy estimate; not a DeepSeek tokenizer)",
        "fixed_prefix_tokens": metadata.prefix_tokens,
        "budget": {
            "limit_rub": format(metadata.budget_limit_rub, "f"),
            "ledger_limit_rub": format(metadata.ledger_limit_rub, "f"),
            "stop_limit_rub": format(metadata.stop_limit_rub, "f"),
            "forecast_max_batch_rub": format(metadata.forecast, "f"),
            "run_cost_rub": None if run_cost is None else format(run_cost, "f"),
            "run_exact_cost_rub": format(exact_cost_total, "f"),
            "run_reserved_cost_rub": format(reserved_cost_total, "f"),
            "completed_question_cost_rub": None if completed_cost is None else format(completed_cost, "f"),
            "cumulative_cost_rub": format(metadata.ledger.total_cost_rub, "f"),
            "cumulative_exact_cost_rub": format(ledger_exact_total, "f"),
            "cumulative_reserved_cost_rub": format(ledger_reserved_total, "f"),
            "unpriced_requests": metadata.ledger.unpriced_requests,
            "estimated_records": sum(entry.get("estimated") is True for entry in metadata.ledger.entries),
            "exact_records": sum(entry.get("estimated") is False for entry in metadata.ledger.entries),
        },
        "metrics": {
            "quality_evaluable_question_runs": len(quality_runs),
            "connection_failure_question_runs": len(connection_failure_runs),
            "finish_reason_length_responses": sum(run.finish_reason_length_responses for run in runs),
            "correct_tools_pct": _percentage(sum(run.tools_correct for run in quality_runs), len(quality_runs)),
            "correct_confirmation_pct": _percentage(
                sum(run.confirmation_correct for run in quality_runs), len(quality_runs)
            ),
            "correct_action_args_pct": _percentage(
                sum(run.action_args_correct for run in quality_runs),
                sum(run.action_args_expected for run in quality_runs),
            ),
            "security_errors": security_errors,
            "blocked_attempts": {
                name: sum(run.blocked_attempts[name] for run in runs)
                for name in ("action_outside_rights", "wrong_recipient")
            },
            "latency_s": {"mean": _mean(latencies), "p95": _p95(latencies)},
            "cost_per_question_rub": {
                "mean": _mean([float(cost) for cost in priced_costs]) if all_costs_known else None,
                "p95": _p95([float(cost) for cost in priced_costs]) if all_costs_known else None,
            },
            "exact_cost_per_question_rub": {
                "mean": None if exact_cost_per_question is None else round(float(exact_cost_per_question), 6),
                "total": format(exact_cost_total, "f"),
            },
            "reserved_cost_per_question_rub": {
                "mean": None if reserved_cost_per_question is None else round(float(reserved_cost_per_question), 6),
                "total": format(reserved_cost_total, "f"),
            },
            "tokens_per_question": {
                "total_p95": _p95([float(run.input_tokens + run.output_tokens) for run in runs]),
                "input": _mean([float(run.input_tokens) for run in runs]),
                "cached": _mean([float(run.cached_tokens) for run in runs]),
                "output": _mean([float(run.output_tokens) for run in runs]),
                "reasoning": _mean([float(run.reasoning_tokens) for run in runs]),
            },
            "cache_share_pct": _percentage(cached_tokens, input_tokens),
            "model_requests_per_question": _mean([float(run.requests) for run in runs]),
            "russian_quality_mean_1_to_5": None,
            "usage_totals": {
                "requests": requests,
                "input_tokens": input_tokens,
                "cached_tokens": cached_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
        },
        "cases": cases,
    }


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator * PERCENTAGE_MULTIPLIER / denominator, 2) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered_values = sorted(values)
    return round(ordered_values[max(math.ceil(len(ordered_values) * 0.95) - 1, 0)], 4)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paid Polza agent evaluations using synthetic cases.")
    parser.add_argument("--model", default=TARGET_MODEL)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--case", action="append", dest="case", help="Limit the run to a case name; repeatable.")
    parser.add_argument("--tool", help="Run all positive and negative cases associated with one registered tool.")
    parser.add_argument("--smoke", action="store_true", help="Run key scenarios plus every security-marked case.")
    parser.add_argument(
        "--max-run-cost-rub",
        type=Decimal,
        help="Stop before a request if its conservative reserve would exceed this run's ledger increase.",
    )
    parser.add_argument("--report", type=Path, help="Write aggregate-only JSON to this path.")
    argv = sys.argv[1:]
    args = parser.parse_args(argv[1:] if argv[:1] == ["--"] else argv)
    if args.model != TARGET_MODEL:
        parser.error(f"This budget-controlled harness only prices {TARGET_MODEL}")
    if args.max_run_cost_rub is not None and args.max_run_cost_rub <= 0:
        parser.error("--max-run-cost-rub must be greater than zero")
    if args.case and (args.tool is not None or args.smoke):
        parser.error("--case cannot be combined with --tool or --smoke")
    if args.tool is not None and args.smoke:
        parser.error("--tool and --smoke cannot be combined")
    return args


def _run_budget(
    requested_run_cost_rub: Decimal | None,
    ledger_cost_rub: Decimal,
    stop_limit_rub: Decimal = STOP_BUDGET_RUB,
    global_limit_rub: Decimal = MAX_BUDGET_RUB,
) -> Decimal:
    """Resolve a per-invocation cap without weakening the persisted STOP threshold."""
    if requested_run_cost_rub is None:
        return min(stop_limit_rub, global_limit_rub)
    if requested_run_cost_rub <= 0:
        raise ValueError("--max-run-cost-rub must be greater than zero")
    return min(stop_limit_rub, global_limit_rub, ledger_cost_rub + requested_run_cost_rub)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run_evals()))
    except BudgetStopError as error:
        print(f"STOP: {error}")
        sys.exit(2)
