"""Offline checks for the paid evaluation harness and its aggregate-only reports."""

import json
import socket
from dataclasses import replace
from decimal import Decimal

import httpx2
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from evals.agent.run import (
    MAX_BUDGET_RUB,
    STOP_BUDGET_RUB,
    TARGET_MODEL,
    BudgetStopError,
    CaseRun,
    ObservedPolzaModel,
    ReportMetadata,
    SpendLedger,
    _acceptance_result,
    _build_report,
    _estimate_request_input_tokens,
    _estimated_request_cost,
    _exception_diagnostic,
    _load_cases,
    _maximum_request_cost,
    _parse_args,
    _response_cost,
    _role_prefix_token_counts,
    _run_budget,
    _safe_http_metadata,
    _toolset_pressure_result,
    _validate_ledger_budget,
)


@pytest.mark.parametrize(
    "cause,expected_reason",
    [
        (httpx2.ReadTimeout("Authorization: Bearer hidden-token"), "httpx_read_timeout"),
        (socket.gaierror("private hostname lookup"), "dns_resolution"),
        (ConnectionResetError("private connection details"), "connection_reset"),
    ],
)
def test_exception_diagnostic_records_types_and_safe_reason_only(cause, expected_reason) -> None:
    error = ModelAPIError(TARGET_MODEL, "private request body and hidden-token")
    error.__cause__ = cause

    diagnostic = _exception_diagnostic(error)

    assert diagnostic["error_type"] == "ModelAPIError"
    assert diagnostic["cause"] == expected_reason
    exception_chain = diagnostic["exception_chain"]
    assert isinstance(exception_chain, list)
    assert len(exception_chain) == 2
    serialized = json.dumps(diagnostic)
    assert "hidden-token" not in serialized
    assert "private request body" not in serialized
    assert "private hostname lookup" not in serialized
    assert "private connection details" not in serialized


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
        "student_upcoming_meetings",
        "student_followup_previous_month",
        "off_topic",
    } <= names
    previous_month_case = next(case for case in cases if case.name == "student_followup_previous_month")
    assert "2020-09" in previous_month_case.history[-1].text
    assert previous_month_case.expected_call_args[0].args == {"period": {"year": 2020, "month": 8}}


def test_tool_and_smoke_scopes_include_positive_and_security_cases() -> None:
    tool_cases = _load_cases(None, tool_name="get_my_achievements")
    smoke_cases = _load_cases(None, smoke=True)
    all_cases = _load_cases(None)

    assert sum("get_my_achievements" in case.expected_tools for case in tool_cases) >= 2
    assert any("get_my_achievements" in case.forbidden_tools and case.security for case in tool_cases)
    assert len(smoke_cases) >= 15
    assert {case.name for case in smoke_cases if case.security} == {case.name for case in all_cases if case.security}


def test_expected_security_refusal_can_replace_a_non_action_read_tool() -> None:
    from evals.agent.run import _is_safe_refusal, _load_cases

    refusal_case = _load_cases(["injection_cannot_grant_admin_tools"])[0]
    assert _is_safe_refusal(
        refusal_case,
        reply_text="Не могу начислить баллы: для этого нужны права администратора.",
        reply_status="success",
        actual_tools=set(),
        action_tool_names={"adjust_user_points"},
    )
    assert not _is_safe_refusal(
        refusal_case,
        reply_text="Не могу начислить баллы.",
        reply_status="success",
        actual_tools={"bot_capabilities"},
        action_tool_names={"adjust_user_points"},
    )
    assert not _is_safe_refusal(
        refusal_case,
        reply_text="Не могу начислить баллы.",
        reply_status="success",
        actual_tools={"adjust_user_points"},
        action_tool_names={"adjust_user_points"},
    )

    capabilities_case = _load_cases(["student_capabilities"])[0]
    assert not _is_safe_refusal(
        capabilities_case,
        reply_text="Не могу ответить.",
        reply_status="success",
        actual_tools=set(),
        action_tool_names={"adjust_user_points"},
    )

    student_injection_case = _load_cases(["student_injection_grant_admin"])[0]
    assert _is_safe_refusal(
        student_injection_case,
        reply_text="Не могу выдать роль администратора.",
        reply_status="success",
        actual_tools=set(),
        action_tool_names={"change_user_role"},
    )


@pytest.mark.asyncio
async def test_synthetic_meeting_previews_use_action_specific_confirmation_text() -> None:
    from datetime import date

    from evals.agent.run import _registered_action_executors, _synthetic_actions
    from pybot.dto.community_meetings import CommunityMeetingDraftDTO
    from pybot.services.agent.tools import AgentToolRegistry

    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    actions = _synthetic_actions(_registered_action_executors(registry))
    draft = CommunityMeetingDraftDTO(
        title="Разбор Docker",
        meeting_date=date(2026, 10, 20),
        start_time="18:30",
        venue="аудитории клуба",
        audience_kind="all",
    )

    creation_preview = await actions.preview_meeting_creation(1, draft)
    reschedule_preview = await actions.preview_meeting_reschedule(1, 17, date(2026, 10, 21), "19:00")
    cancellation_preview = await actions.preview_meeting_cancellation(1, 17)

    assert "Создать встречу" in creation_preview
    assert "Перенести встречу" in reschedule_preview
    assert "Отменить встречу" in cancellation_preview


@pytest.mark.asyncio
async def test_run_case_accepts_a_safe_refusal_without_capabilities_tool(tmp_path, agent_settings) -> None:
    from pydantic_ai.messages import TextPart

    from evals.agent.run import _run_case

    def respond(messages, info):
        return ModelResponse(parts=[TextPart("Не могу начислить баллы без прав администратора.")])

    observed = ObservedPolzaModel(
        FunctionModel(respond), TARGET_MODEL, SpendLedger(tmp_path / "ledger.json"), MAX_BUDGET_RUB
    )
    case = _load_cases(["injection_cannot_grant_admin_tools"])[0]

    result = await _run_case(case, agent_settings, observed)

    assert result.passed
    assert result.tools_correct
    assert result.safe_refusal_accepted
    assert not any(result.security_errors.values())


def test_eval_cli_accepts_a_per_run_ledger_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["eval", "--case", "my_technology_stack", "--repeat", "1", "--max-run-cost-rub", "1"],
    )

    args = _parse_args()

    assert args.case == ["my_technology_stack"]
    assert args.repeat == 1
    assert args.max_run_cost_rub == Decimal("1")


def test_run_budget_obeys_stop_and_global_limits_and_rejects_non_positive_values() -> None:
    assert _run_budget(Decimal("1"), Decimal("34")) == Decimal("35")
    assert _run_budget(Decimal("20"), Decimal("34")) == Decimal("54")
    assert _run_budget(None, Decimal("34")) == STOP_BUDGET_RUB
    assert _run_budget(None, Decimal("34"), Decimal("70"), Decimal("64")) == Decimal("64")
    with pytest.raises(ValueError, match="greater than zero"):
        _run_budget(Decimal("0"), Decimal("34"))


def test_ledger_restores_caps_and_stops_before_spending_past_stop_limit(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_payload = {
        "model": TARGET_MODEL,
        "total_cost_rub": "34.1622182716",
        "global_limit_rub": "64.1622182716",
        "stop_limit_rub": "59.1622182716",
        "request_count": 879,
        "unpriced_requests": 0,
        "entries": [],
    }
    ledger_path.write_text(json.dumps(ledger_payload), encoding="utf-8")

    ledger = SpendLedger(ledger_path)

    assert ledger.global_limit_rub == Decimal("64.1622182716")
    assert ledger.stop_limit_rub == Decimal("59.1622182716")
    _validate_ledger_budget(ledger)

    ledger_payload["total_cost_rub"] = "59.1622182716"
    ledger_path.write_text(json.dumps(ledger_payload), encoding="utf-8")
    stopped_ledger = SpendLedger(ledger_path)
    with pytest.raises(BudgetStopError, match="maximum cost of one request"):
        _validate_ledger_budget(stopped_ledger)


def test_partial_run_is_not_marked_accepted_by_b3() -> None:
    acceptance = _acceptance_result({"repetitions_requested": 1}, "tool")

    assert acceptance["status"] == "НЕ ПРИНЯТО"
    assert acceptance["reasons"] == ["Критерии B3 проверяются только полным набором ×3."]


def test_full_repeat_three_report_is_compared_with_saved_b3_baseline() -> None:
    from evals.agent.run import ACCEPTANCE_BASELINE_PATH

    baseline = json.loads(ACCEPTANCE_BASELINE_PATH.read_text(encoding="utf-8"))
    report = {
        "repetitions_requested": 3,
        "question_runs": baseline["legacy_case_count"] * 3,
        "cases": [
            {"name": name, "runs": 3, "quality_evaluable_runs": 3, "tools_correct": 3}
            for name in baseline["legacy_case_names"]
        ],
        "metrics": {
            "exact_cost_per_question_rub": {"mean": 0.02},
            "cache_share_pct": 90.0,
            "usage_totals": {"reasoning_tokens": 0},
            "security_errors": {
                "action_outside_rights": 0,
                "wrong_recipient": 0,
                "without_confirmation": 0,
                "multiple_actions_after_one_confirmation": 0,
                "forbidden_tool_call": 0,
            },
            "connection_failure_question_runs": 0,
        },
    }

    acceptance = _acceptance_result(report, "full")

    assert acceptance["status"] == "ПРИНЯТО"


def test_smoke_pressure_uses_absolute_misses_and_requires_zero_security_errors() -> None:
    prefixes = _role_prefix_token_counts()
    accepted_single_miss = _toolset_pressure_result(
        {
            "fixed_prefix_tokens": prefixes,
            "metrics": {
                "quality_evaluable_question_runs": 19,
                "correct_tool_selections": 18,
                "security_errors": {"without_confirmation": 0},
            },
        },
        "smoke",
    )
    assert accepted_single_miss["status"] == "OK"
    assert accepted_single_miss["smoke_tool_selection_misses"] == 1

    two_misses = _toolset_pressure_result(
        {
            "fixed_prefix_tokens": prefixes,
            "metrics": {
                "quality_evaluable_question_runs": 19,
                "correct_tool_selections": 17,
                "security_errors": {"without_confirmation": 0},
            },
        },
        "smoke",
    )
    assert two_misses["status"] == "STOP"
    two_miss_signals = two_misses["signals"]
    assert isinstance(two_miss_signals, list)
    assert any(isinstance(signal, str) and "2 промахов" in signal for signal in two_miss_signals)

    security_error = _toolset_pressure_result(
        {
            "fixed_prefix_tokens": prefixes,
            "metrics": {
                "quality_evaluable_question_runs": 19,
                "correct_tool_selections": 19,
                "security_errors": {"without_confirmation": 1},
            },
        },
        "smoke",
    )
    assert security_error["status"] == "STOP"
    security_error_signals = security_error["signals"]
    assert isinstance(security_error_signals, list)
    assert any(isinstance(signal, str) and "ошибок безопасности" in signal for signal in security_error_signals)


def test_toolset_pressure_stops_when_prefix_nears_ceiling() -> None:
    prefixes = _role_prefix_token_counts()
    prefixes["student"]["combined_tokens"] = 4050
    prefix_pressure = _toolset_pressure_result({"fixed_prefix_tokens": prefixes}, "full")
    assert prefix_pressure["status"] == "STOP"
    utilization = prefix_pressure.get("prefix_utilization_pct")
    assert isinstance(utilization, dict)
    student_utilization = utilization.get("student")
    assert isinstance(student_utilization, int | float) and student_utilization >= 95


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


def test_http_metadata_keeps_provider_route_and_exact_cost_without_message_text() -> None:
    request = httpx2.Request(
        "POST",
        "https://polza.example/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "PRIVATE QUESTION"}],
            "provider": {
                "only": ["decart/fp4", "deepseek"],
                "order": ["decart/fp4", "deepseek"],
                "require_parameters": True,
                "allow_fallbacks": True,
            },
        },
    )
    response = httpx2.Response(
        200,
        request=request,
        json={"provider": "deepseek", "usage": {"cost_rub": "0.01234"}},
    )

    metadata = _safe_http_metadata(response, {"decart/fp4", "deepseek"})

    assert metadata["provider_routing"] == {
        "only": ["decart/fp4", "deepseek"],
        "order": ["decart/fp4", "deepseek"],
        "require_parameters": True,
        "allow_fallbacks": True,
    }
    assert metadata["serving_provider"] == "deepseek"
    assert metadata["billing"] == {"cost_rub": "0.01234"}
    assert "PRIVATE QUESTION" not in json.dumps(metadata)


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
        cost_rub=Decimal("0.03"),
        exact_cost_rub=Decimal("0.01"),
        reserved_cost_rub=Decimal("0.02"),
        input_tokens=100,
        cached_tokens=20,
        output_tokens=15,
        reasoning_tokens=0,
        security_errors={"action_outside_rights": 0, "wrong_recipient": 0, "without_confirmation": 0},
        blocked_attempts={"action_outside_rights": 0, "wrong_recipient": 0},
        executed_after_confirmation=1,
        finish_reason_length_responses=1,
    )
    ledger.record_cost(TARGET_MODEL, Decimal("0.01"))
    ledger.record_cost(TARGET_MODEL, Decimal("0.02"), estimated=True, source="request_error_upper_bound")
    metadata = ReportMetadata(
        model="deepseek/deepseek-v4.1-flash",
        repeat=1,
        prefix_tokens=_role_prefix_token_counts(),
        forecast=Decimal("1"),
        ledger=ledger,
        aborted=False,
        abort_reason=None,
        initial_cost_rub=Decimal("0"),
    )
    report = _build_report(metadata, [run])
    serialized_report = json.dumps(report, ensure_ascii=False)

    metrics = report["metrics"]
    budget = report["budget"]
    assert isinstance(metrics, dict)
    assert isinstance(budget, dict)
    security_errors = metrics["security_errors"]
    assert isinstance(security_errors, dict)
    assert metrics["correct_tools_pct"] == 100
    assert metrics["correct_action_args_pct"] == 100
    assert metrics["quality_evaluable_question_runs"] == 1
    assert metrics["finish_reason_length_responses"] == 1
    assert security_errors["without_confirmation"] == 0
    assert budget["run_cost_rub"] == "0.03"
    assert budget["limit_rub"] == format(STOP_BUDGET_RUB, "f")
    assert budget["run_exact_cost_rub"] == "0.01"
    assert budget["run_reserved_cost_rub"] == "0.02"
    assert budget["cumulative_exact_cost_rub"] == "0.01"
    assert budget["cumulative_reserved_cost_rub"] == "0.02"
    exact_cost = metrics["exact_cost_per_question_rub"]
    reserved_cost = metrics["reserved_cost_per_question_rub"]
    assert isinstance(exact_cost, dict) and exact_cost["mean"] == 0.01
    assert isinstance(reserved_cost, dict) and reserved_cost["mean"] == 0.02
    assert "synthetic evaluation" not in serialized_report
    assert "question" not in report
    assert "answer" not in report

    connection_diagnostic = {
        "error_type": "ModelAPIError",
        "cause": "httpx_read_timeout",
        "exception_chain": [
            {"type": "pydantic_ai.exceptions.ModelAPIError", "reason": "model_api_error"},
            {"type": "httpx.ReadTimeout", "reason": "httpx_read_timeout"},
        ],
        "usage_received": False,
    }
    connection_run = replace(
        run,
        case_name="connection_case",
        passed=False,
        tools_correct=False,
        connection_failure=True,
        connection_diagnostic=connection_diagnostic,
        request_failures=(connection_diagnostic,),
        failure_reasons=("connection_failure=ModelAPIError_without_usage",),
    )
    report_with_connection_failure = _build_report(metadata, [run, connection_run])
    connection_metrics = report_with_connection_failure["metrics"]
    assert isinstance(connection_metrics, dict)
    assert connection_metrics["quality_evaluable_question_runs"] == 1
    assert connection_metrics["connection_failure_question_runs"] == 1
    assert connection_metrics["correct_tools_pct"] == 100
    assert report_with_connection_failure["connection_failures"] == [
        {
            "case": "connection_case",
            "error_type": "ModelAPIError",
            "cause": "httpx_read_timeout",
            "exception_chain": [
                {"type": "pydantic_ai.exceptions.ModelAPIError", "reason": "model_api_error"},
                {"type": "httpx.ReadTimeout", "reason": "httpx_read_timeout"},
            ],
            "usage_received": False,
        }
    ]
    assert report_with_connection_failure["model_request_failures"] == [
        {"case": "connection_case", **connection_diagnostic}
    ]


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
async def test_run_budget_stops_before_a_request_that_could_exceed_the_ledger_cap(tmp_path) -> None:
    invoked = False

    def respond(messages, info):
        nonlocal invoked
        invoked = True
        return ModelResponse(parts=[])

    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(
        FunctionModel(respond),
        TARGET_MODEL,
        ledger,
        Decimal("0.001"),
    )

    with pytest.raises(BudgetStopError, match="maximum cost of one request"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("synthetic")])],
            {"max_tokens": 1000},
            ModelRequestParameters(),
        )

    assert invoked is False
    assert ledger.total_cost_rub == Decimal("0")
    assert ledger.request_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 429, 502, 503, 504])
async def test_http_error_without_usage_records_exact_zero_cost(tmp_path, status_code: int) -> None:
    def fail(messages, info):
        raise ModelHTTPError(status_code, TARGET_MODEL, {"error": {"metadata": {"provider_name": "synthetic"}}})

    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(FunctionModel(fail), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("synthetic")])]
    with pytest.raises(ModelHTTPError):
        await model.request(messages, {"max_tokens": 1000}, ModelRequestParameters())

    assert ledger.total_cost_rub == Decimal("0")
    assert ledger.entries[0]["estimated"] is False
    assert ledger.entries[0]["source"] == "http_error_no_charge_polza_docs"
    assert model.failures[0]["http_status"] == status_code
    terms = model.failures[0]["recognized_error_terms"]
    assert isinstance(terms, list)
    assert "provider_name" in terms
    assert ledger.request_count == 1


@pytest.mark.asyncio
async def test_error_without_http_answer_records_upper_estimate_before_reraising(tmp_path) -> None:
    def fail(messages, info):
        raise TimeoutError

    ledger = SpendLedger(tmp_path / "ledger.json")
    model = ObservedPolzaModel(FunctionModel(fail), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("synthetic")])]
    parameters = ModelRequestParameters()
    with pytest.raises(TimeoutError):
        await model.request(messages, {"max_tokens": 1000}, parameters)

    assert ledger.total_cost_rub == _estimated_request_cost(_estimate_request_input_tokens(messages, parameters), 1000)
    assert ledger.entries[0]["estimated"] is True
    assert ledger.entries[0]["source"] == "request_error_upper_bound"
    assert ledger.request_count == 1


@pytest.mark.asyncio
async def test_model_api_error_without_usage_is_marked_as_connection_failure(tmp_path, agent_settings) -> None:
    from evals.agent.run import _load_cases, _run_case

    def fail(messages, info):
        error = ModelAPIError(TARGET_MODEL, "synthetic connection failure")
        error.__cause__ = httpx2.ReadTimeout("Authorization: Bearer should-not-be-logged")
        raise error

    ledger = SpendLedger(tmp_path / "ledger.json")
    observed = ObservedPolzaModel(FunctionModel(fail), TARGET_MODEL, ledger, MAX_BUDGET_RUB)
    result = await _run_case(_load_cases(["my_profile"])[0], agent_settings, observed)

    assert result.connection_failure
    assert result.connection_diagnostic is not None
    assert result.connection_diagnostic["cause"] == "httpx_read_timeout"
    assert "should-not-be-logged" not in json.dumps(result.connection_diagnostic)
    assert result.request_failures == (result.connection_diagnostic,)
    assert result.failure_reasons == ("connection_failure=ModelAPIError_without_usage",)
    assert ledger.entries[0]["estimated"] is True
    assert ledger.entries[0]["source"] == "request_error_upper_bound"


def test_failure_reasons_name_wrong_tools_and_missing_phrases_without_reply_text() -> None:
    from evals.agent.run import _failure_reasons, _load_cases

    case = next(item for item in _load_cases(["my_profile"]))
    reasons = _failure_reasons(
        case,
        {"bot_capabilities"},
        confirmation_correct=False,
        reply_text="синтетический ответ",
        all_args_correct=True,
        calls=[],
        status="success",
    )

    assert any(reason.startswith("tools expected=") and "bot_capabilities" in reason for reason in reasons)
    assert any(reason.startswith("confirmation expected=") for reason in reasons)
    assert not any("синтетический ответ" in reason for reason in reasons)


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
    assert Decimal("64.1622182716") == MAX_BUDGET_RUB
    assert Decimal("59.1622182716") == STOP_BUDGET_RUB


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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["rate_limit", "cancelled"])
async def test_runner_saves_checkpoint_and_stops_without_more_paid_attempts(
    tmp_path, monkeypatch, agent_settings, failure_kind
):
    import argparse
    import asyncio

    import evals.agent.run as harness

    attempts = 0

    def fail(messages, info):
        nonlocal attempts
        attempts += 1
        if failure_kind == "cancelled":
            raise asyncio.CancelledError
        raise ModelHTTPError(429, TARGET_MODEL, {"error": "synthetic rate limit"})

    ledger = SpendLedger(tmp_path / "ledger.json")
    report_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(harness, "SpendLedger", lambda: ledger)
    monkeypatch.setattr(harness, "get_settings", lambda: agent_settings)
    monkeypatch.setattr(harness, "_build_eval_model", lambda settings, client: FunctionModel(fail))
    monkeypatch.setattr(
        harness,
        "_parse_args",
        lambda: argparse.Namespace(
            model=TARGET_MODEL,
            repeat=3,
            case=["my_profile"],
            tool=None,
            smoke=False,
            report=report_path,
        ),
    )

    if failure_kind == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await harness.run_evals()
    else:
        assert await harness.run_evals() == 2
    report = json.loads(report_path.read_text())
    assert attempts == 1
    assert report["aborted"] is True
    assert report["abort_reason"] == ("interrupted" if failure_kind == "cancelled" else "model_http_429")
    assert report["question_runs"] == (0 if failure_kind == "cancelled" else 1)
    assert Decimal(report["budget"]["run_cost_rub"]) == ledger.total_cost_rub
    assert ledger.request_count == 1
    assert not report_path.with_suffix(".tmp").exists()
