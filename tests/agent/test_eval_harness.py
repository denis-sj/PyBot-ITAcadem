"""Offline checks for the paid evaluation harness and its aggregate-only reports."""

import json
from decimal import Decimal

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from evals.agent.run import (
    MAX_BUDGET_RUB,
    CaseRun,
    ObservedPolzaModel,
    TARGET_MODEL,
    _estimated_request_cost,
    ReportMetadata,
    SpendLedger,
    _build_report,
    _estimate_request_input_tokens,
    _load_cases,
    _maximum_request_cost,
    _response_cost,
    _role_prefix_token_counts,
)


def test_eval_cases_cover_required_scenarios_and_have_unique_names() -> None:
    cases = _load_cases(None)
    names = {case.name for case in cases}

    assert len(cases) >= 30
    assert len(names) == len(cases)
    assert {
        "admin_points_by_username",
        "admin_points_by_full_name",
        "admin_role_change_confirmation",
        "admin_competence_change_confirmation",
        "admin_attendance_time_edit_confirmation",
        "admin_ambiguous_recipient_requests_id",
        "admin_broadcast_requires_audience",
        "admin_two_actions_one_confirmation",
        "student_injection_grant_admin",
        "student_followup_previous_month",
        "off_topic",
    } <= names


def test_request_cost_guard_reserves_forecast_maximum() -> None:
    assert _maximum_request_cost(1_000) == Decimal("0.19045")
    assert _maximum_request_cost(1_000) < MAX_BUDGET_RUB


def test_request_input_estimator_counts_messages_and_tool_schemas() -> None:
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("synthetic evaluation")])]
    no_tools = _estimate_request_input_tokens(messages, ModelRequestParameters())
    with_tool = _estimate_request_input_tokens(
        messages,
        ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name="synthetic_tool",
                    description="Synthetic tool schema",
                    parameters_json_schema={"type": "object", "properties": {"value": {"type": "string"}}},
                )
            ]
        ),
    )

    assert no_tools > 0
    assert with_tool > no_tools


def test_response_cost_requires_a_valid_polza_cost_field() -> None:
    priced_response = ModelResponse(parts=[], provider_details={"polza_usage": {"cost_rub": "0.012"}})
    unpriced_response = ModelResponse(parts=[], provider_details={"polza_usage": {"input_tokens": 10}})

    assert _response_cost(priced_response) == Decimal("0.012")
    assert _response_cost(unpriced_response) is None


def test_report_has_aggregate_metrics_without_case_text(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json")
    run = CaseRun(
        case_name="synthetic_case",
        passed=True,
        tools_correct=True,
        confirmation_correct=True,
        phrases_correct=True,
        action_args_correct=1,
        action_args_expected=1,
        requests=1,
        latency_s=0.25,
        cost_rub=Decimal("0.01"),
        input_tokens=100,
        cached_tokens=20,
        output_tokens=15,
        reasoning_tokens=0,
        security_errors={"action_outside_rights": 0, "wrong_recipient": 0, "without_confirmation": 0},
        blocked_attempts={"action_outside_rights": 0, "wrong_recipient": 0},
        executed_after_confirmation=1,
    )
    report = _build_report(
        ReportMetadata(
            model="deepseek/deepseek-v4.1-flash",
            repeat=1,
            prefix_tokens=_role_prefix_token_counts(),
            forecast=Decimal("1"),
            ledger=ledger,
            aborted=False,
            abort_reason=None,
        ),
        [run],
    )
    serialized_report = json.dumps(report, ensure_ascii=False)

    metrics = report["metrics"]
    budget = report["budget"]
    assert isinstance(metrics, dict)
    assert isinstance(budget, dict)
    security_errors = metrics["security_errors"]
    assert isinstance(security_errors, dict)
    assert metrics["correct_tools_pct"] == 100
    assert metrics["correct_action_args_pct"] == 100
    assert security_errors["without_confirmation"] == 0
    assert budget["run_cost_rub"] == "0.01"
    assert "synthetic evaluation" not in serialized_report
    assert "question" not in report
    assert "answer" not in report


@pytest.mark.asyncio
async def test_missing_cost_prices_usage_tokens_and_marks_estimate(tmp_path) -> None:
    def respond(messages, info):
        return ModelResponse(parts=[], usage=RequestUsage(input_tokens=1000, output_tokens=100))

    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(FunctionModel(respond), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    await model.request(
        [ModelRequest(parts=[UserPromptPart("synthetic")])], {"max_tokens": 1000}, ModelRequestParameters()
    )

    assert ledger.total_cost_rub == Decimal("0.007407")
    assert ledger.entries == [{"cost_rub": "0.007407", "estimated": True, "source": "usage_tokens"}]
    assert ledger.request_count == 1


@pytest.mark.asyncio
async def test_error_without_usage_records_upper_estimate_before_reraising(tmp_path) -> None:
    def fail(messages, info):
        raise ModelHTTPError(400, TARGET_MODEL, {"error": "synthetic failure"})

    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(FunctionModel(fail), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("synthetic")])]
    parameters = ModelRequestParameters()
    with pytest.raises(ModelHTTPError):
        await model.request(messages, {"max_tokens": 1000}, parameters)

    assert ledger.total_cost_rub == _estimated_request_cost(_estimate_request_input_tokens(messages, parameters), 1000)
    assert ledger.entries[0]["estimated"] is True
    assert ledger.entries[0]["source"] == "request_error_upper_bound"
    assert model.failures[0]["http_status"] == 400
    assert ledger.request_count == 1


@pytest.mark.asyncio
async def test_success_without_usage_records_upper_estimate(tmp_path, monkeypatch) -> None:
    def respond(messages, info):
        return ModelResponse(parts=[])

    async def no_usage(messages, settings, parameters):
        return ModelResponse(parts=[])

    base_model = FunctionModel(respond)
    monkeypatch.setattr(base_model, "request", no_usage)
    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(base_model, TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("synthetic")])]
    parameters = ModelRequestParameters()
    await model.request(messages, {"max_tokens": 1000}, parameters)

    assert ledger.total_cost_rub == _estimated_request_cost(_estimate_request_input_tokens(messages, parameters), 1000)
    assert ledger.entries[0]["source"] == "missing_usage_upper_bound"
    assert ledger.entries[0]["estimated"] is True


def test_checked_tariffs_use_provider_cache_price_or_unknown_upper_bound() -> None:
    from evals.agent.pricing import ProviderCatalog

    catalog = ProviderCatalog.model_validate(
        {
            "id": TARGET_MODEL,
            "providers": [
                {
                    "name": "cheap",
                    "pricing": {
                        "prompt_per_million": "10",
                        "completion_per_million": "20",
                        "input_cache_read_per_million": "1",
                    },
                },
                {"name": "expensive", "pricing": {"prompt_per_million": "100", "completion_per_million": "200"}},
            ],
        }
    )
    assert catalog.tariff("cheap").estimate(1000, 100, 500) == Decimal("0.0075")
    assert catalog.tariff(None).estimate(1000, 100, 500) == Decimal("0.12")
    assert MAX_BUDGET_RUB == Decimal("30")


def test_provider_matrix_redacts_key_and_bearer_error_body() -> None:
    from evals.agent.provider_matrix import _redact

    private_key = "synthetic-key"
    redacted = _redact("error synthetic-key Authorization: Bearer private-token sk-provider-token", private_key)
    assert "synthetic-key" not in redacted
    assert "private-token" not in redacted
    assert "sk-provider-token" not in redacted


def test_reports_count_exact_and_estimated_ledger_records(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json")
    ledger.record_cost(TARGET_MODEL, Decimal("0.01"))
    ledger.record_cost(TARGET_MODEL, Decimal("0.02"), estimated=True, source="usage_tokens")
    restored = json.loads(ledger.path.read_text())
    assert restored["exact_records"] == 1
    assert restored["estimated_records"] == 1
    assert restored["total_cost_rub"] == "0.03"


@pytest.mark.asyncio
async def test_duplicate_correct_read_tool_is_not_wrong_tool_selection(tmp_path, agent_settings) -> None:
    from pydantic_ai.messages import TextPart, ToolCallPart

    from evals.agent.run import _run_case

    requests = 0

    def respond(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("get_my_profile", {}, tool_call_id="synthetic-first"),
                    ToolCallPart("get_my_profile", {}, tool_call_id="synthetic-second"),
                ]
            )
        return ModelResponse(parts=[TextPart("75 академических и 12 репутационных баллов.")])

    ledger = SpendLedger(tmp_path / "ledger.json")
    observed = ObservedPolzaModel(FunctionModel(respond), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    case = _load_cases(["my_profile"])[0]
    result = await _run_case(case, agent_settings, observed)
    assert result.tools_correct
    assert result.passed
    assert result.requests == 2


def test_matrix_default_variant_clears_runtime_provider_and_reasoning_defaults():
    from evals.agent.provider_matrix import ProbeVariant, _model_settings

    variant = ProbeVariant("none", "default", "auto", None, None)
    assert _model_settings(variant)["extra_body"] == {}
