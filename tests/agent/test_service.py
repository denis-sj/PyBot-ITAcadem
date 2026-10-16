import asyncio
import json
import re
from datetime import datetime
from unittest.mock import Mock

import httpx2
import pytest
from pydantic_ai import RunContext, Tool, ToolDenied
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pybot.core.constants import YEKATERINBURG_TZ
from pybot.dto.agent import AgentReplyStatus
from pybot.services.agent import service as agent_service_module
from pybot.services.agent.service import build_agent_instructions
from pybot.services.agent.tools import AgentDependencies, AgentToolMetadata, AgentToolRegistry, RegisteredAgentTool
from tests.agent.conftest import build_service


def test_relative_month_instruction_anchors_followup_to_last_assistant_answer():
    instructions = build_agent_instructions()

    assert "месяца, обсуждавшегося в последнем ответе истории" in instructions
    assert "если в том ответе месяц не назван, считай от текущей даты" in instructions
    assert "«позапрошлый» — два месяца до него" in instructions


def test_checked_history_can_answer_an_exact_repeat_without_a_new_read():
    instructions = build_agent_instructions()

    assert "На точную просьбу «повтори» можно повторить последний проверенный ответ из истории без нового чтения" in (
        instructions
    )


def test_technology_stack_instructions_route_reads_and_confirm_only_own_stack_actions():
    instructions = build_agent_instructions()

    assert "На запрос «мой стек»" in instructions
    assert "find_technology_helpers" in instructions
    assert "set_my_technology" in instructions
    assert "remove_my_technology" in instructions
    assert "только для стека автора" in instructions
    assert "не утверждай, что стек изменён до результата" in instructions


@pytest.mark.asyncio
async def test_capabilities_use_service_roles_and_persist_only_final_text(agent_settings, conversation):
    service, users, memory = build_service(agent_settings)
    result = await service.answer(conversation, "Что умеет бот?")
    assert result.text == "Откройте /profile или /visits"
    assert result.confirmation_id is None
    users.find_all_user_roles.assert_awaited()
    memory.append.assert_awaited_once_with(conversation, "Что умеет бот?", result.text)


@pytest.mark.asyncio
async def test_role_prefix_is_stable_and_business_date_is_at_prompt_end(agent_settings, conversation):
    serialized_prefixes = []
    prompts = []
    call_number = 0

    def model(messages, info):
        nonlocal call_number
        call_number += 1
        serialized_prefixes.append(
            json.dumps(
                {
                    "instructions": info.instructions,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters_json_schema,
                            "strict": tool.strict,
                        }
                        for tool in info.function_tools
                    ],
                    "cache_key": info.model_settings.get("openai_prompt_cache_key"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        prompts.append(
            [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)][-1]
        )
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[TextPart("Откройте /profile или /visits")])
        return ModelResponse(parts=[ToolCallPart("bot_capabilities", {}, tool_call_id=f"call-{call_number}")])

    service, _, _ = build_service(agent_settings, model=FunctionModel(model))

    await service.answer(conversation, "Что умеет бот?")
    await service.answer(conversation, "Какие у бота функции?")

    assert len(serialized_prefixes) == 4
    assert len(set(serialized_prefixes)) == 1
    current_date = datetime.now(YEKATERINBURG_TZ).date().isoformat()
    assert all(current_date not in prefix for prefix in serialized_prefixes)
    assert all(prompt.startswith(("Что умеет бот?", "Какие у бота функции?")) for prompt in prompts)
    assert all("Текущая дата (Екатеринбург):" in prompt for prompt in prompts)
    assert all('"cache_key": null' in prefix for prefix in serialized_prefixes)


@pytest.mark.asyncio
async def test_unregistered_and_disabled_never_call_model(agent_settings, conversation, monkeypatch):
    service, users, memory = build_service(agent_settings)
    model_mock = Mock(wraps=service.model.get_model)
    monkeypatch.setattr(service.model, "get_model", model_mock)
    users.find_user_by_telegram_id.return_value = None
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.REGISTRATION_REQUIRED
    model_mock.assert_not_called()
    memory.load.assert_not_awaited()
    agent_settings.agent_enabled = False
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    model_mock.assert_not_called()


@pytest.mark.asyncio
async def test_very_long_question_is_rejected_before_model_or_memory_load(agent_settings, conversation, monkeypatch):
    service, _, memory = build_service(agent_settings)
    model_mock = Mock(wraps=service.model.get_model)
    monkeypatch.setattr(service.model, "get_model", model_mock)

    result = await service.answer(conversation, "x" * 20_000)

    assert result.text == "Сообщение слишком длинное. Сократите его до 4000 символов."
    model_mock.assert_not_called()
    memory.load.assert_not_awaited()
    memory.append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("secret detail"), RuntimeError("secret detail")])
async def test_provider_failure_is_safe(agent_settings, conversation, error, monkeypatch):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(service.model, "get_model", Mock(side_effect=error))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    memory.append.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected_category", "expected_root", "expected_http_status"),
    [
        (httpx2.ReadTimeout("private timeout"), "timeout", "ReadTimeout", None),
        (httpx2.ConnectError("private connection"), "connection", "ConnectError", None),
        (ModelHTTPError(503, "private-model", {"message": "private response"}), "http", "ModelHTTPError", 503),
        (ValueError("private validation data"), "validation", "ValueError", None),
        (RuntimeError("private detail"), "other", "RuntimeError", None),
    ],
)
def test_failure_log_has_safe_diagnostic_fields(
    error, expected_category, expected_root, expected_http_status, monkeypatch
):
    log = Mock()
    monkeypatch.setattr(agent_service_module, "logger", log)

    agent_service_module.AgentService._log_failure(error)

    log.error.assert_called_once()
    message = log.error.call_args.args[0]
    fields = log.error.call_args.kwargs
    assert "event=agent_run status=failed" in message
    assert fields["error_type"] == type(error).__name__
    assert fields["category"] == expected_category
    assert fields["root_exception"] == expected_root
    if expected_http_status is None:
        assert "http_status" not in message
        assert "http_status" not in fields
    else:
        assert fields["http_status"] == expected_http_status
    assert "private" not in repr(log.error.call_args)


def test_failure_log_preserves_wrapped_transport_cause_without_its_text(monkeypatch):
    error = ModelAPIError("private-model", "private API detail")
    error.__cause__ = httpx2.ReadTimeout("private transport detail")
    log = Mock()
    monkeypatch.setattr(agent_service_module, "logger", log)

    agent_service_module.AgentService._log_failure(error)

    assert log.error.call_args.kwargs == {
        "error_type": "ModelAPIError",
        "category": "timeout",
        "root_exception": "ReadTimeout",
    }
    assert "http_status" not in log.error.call_args.args[0]
    assert "private" not in repr(log.error.call_args)


@pytest.mark.asyncio
async def test_numbers_guard_has_separate_safe_event(agent_settings, conversation, monkeypatch):
    service, _, _ = build_service(
        agent_settings,
        model=TestModel(call_tools=[], custom_output_text="У пользователя 991337 баллов"),
    )
    log = Mock()
    monkeypatch.setattr(agent_service_module, "logger", log)

    result = await service.answer(conversation, "Покажи мои баллы")

    assert result.status == AgentReplyStatus.UNAVAILABLE
    assert log.warning.call_count == 2
    for warning_call in log.warning.call_args_list:
        assert warning_call.args == (
            "event=agent_numbers_guard_rejected rejected_count={rejected_count} "
            "list_item_number_count={list_item_number_count} time_or_date_count={time_or_date_count} "
            "dialog_history_count={dialog_history_count} other_count={other_count}",
        )
        assert warning_call.kwargs == {
            "rejected_count": 1,
            "list_item_number_count": 0,
            "time_or_date_count": 0,
            "dialog_history_count": 0,
            "other_count": 1,
        }
    failure_call = log.error.call_args
    assert failure_call is not None
    assert failure_call.kwargs["category"] == "numbers_guard"
    assert failure_call.kwargs["root_exception"] == "AgentNumbersGuardError"
    assert "991337" not in repr(log.mock_calls)


@pytest.mark.asyncio
async def test_numbers_guard_retries_once_with_short_hint_only_after_rejection(agent_settings, conversation):
    model_prompts = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        model_prompts.append(
            [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)][-1]
        )
        if len(model_prompts) == 1:
            return ModelResponse(parts=[TextPart("У вас 991337 баллов")])
        return ModelResponse(parts=[TextPart("Данных о баллах нет")])

    service, _, memory = build_service(agent_settings, model=FunctionModel(model))

    result = await service.answer(conversation, "Сколько у меня баллов?")

    assert result.text == "Данных о баллах нет"
    assert result.status == AgentReplyStatus.SUCCESS
    assert len(model_prompts) == 2
    assert model_prompts[1] == "используй только числа из результатов инструментов, при необходимости вызови инструмент"
    memory.append.assert_awaited_once_with(conversation, "Сколько у меня баллов?", result.text)


@pytest.mark.asyncio
async def test_normal_numbered_answer_uses_one_model_request(agent_settings, conversation):
    model_calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(parts=[TextPart("Уточните параметры:\n1. дату\n2) место")])

    service, _, _ = build_service(agent_settings, model=FunctionModel(model))

    result = await service.answer(conversation, "Создай встречу")

    assert result.text == "Уточните параметры:\n1. дату\n2) место"
    assert result.status == AgentReplyStatus.SUCCESS
    assert model_calls == 1


@pytest.mark.asyncio
async def test_numbers_guard_accepts_the_date_appended_to_the_current_prompt(agent_settings, conversation):
    model_calls = 0
    observed_date = ""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls, observed_date
        model_calls += 1
        current_prompt = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        ][-1]
        date_match = re.search(r"Текущая дата \(Екатеринбург\): (\d{4}-\d{2}-\d{2})", current_prompt)
        assert date_match is not None
        observed_date = date_match.group(1)
        return ModelResponse(parts=[TextPart(f"Сегодня {observed_date}.")])

    service, _, _ = build_service(agent_settings, model=FunctionModel(model))

    result = await service.answer(conversation, "Какая сегодня дата?")

    assert result.text == f"Сегодня {observed_date}."
    assert result.status == AgentReplyStatus.SUCCESS
    assert model_calls == 1


@pytest.mark.asyncio
async def test_screenshot_conversation_chain_accepts_numbered_clarifications(agent_settings, conversation):
    model_calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart("bot_capabilities", {}, tool_call_id="capabilities")])
        if model_calls == 2:
            assert any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
            return ModelResponse(parts=[TextPart("Я помощник.\n1. Профиль\n2) Ближайшие встречи")])
        if model_calls == 3:
            prior_questions = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            ]
            prior_answers = [
                part.content for message in messages for part in message.parts if isinstance(part, TextPart)
            ]
            assert any(question == "КТО ТЫ" for question in prior_questions)
            assert any("1. Профиль\n2) Ближайшие встречи" in answer for answer in prior_answers)
            return ModelResponse(parts=[TextPart("Чтобы подготовить встречу:\n1. Уточните дату\n2) Уточните место")])
        return ModelResponse(parts=[TextPart("Уточните название и место, время 13:00 учту.")])

    service, _, memory = build_service(agent_settings, model=FunctionModel(model))
    memory.load.side_effect = [
        [],
        [
            Mock(role="user", text="КТО ТЫ"),
            Mock(role="assistant", text="Я помощник.\n1. Профиль\n2) Ближайшие встречи"),
        ],
        [
            Mock(role="user", text="КТО ТЫ"),
            Mock(role="assistant", text="Я помощник.\n1. Профиль\n2) Ближайшие встречи"),
            Mock(role="user", text="хочу создать встречу и созвать всех менторов"),
            Mock(role="assistant", text="Чтобы подготовить встречу:\n1. Уточните дату\n2) Уточните место"),
        ],
    ]

    first = await service.answer(conversation, "КТО ТЫ")
    second = await service.answer(conversation, "хочу создать встречу и созвать всех менторов")
    third = await service.answer(
        conversation,
        "хочу создать встречу и созвать всех менторов завтра в 13:00",
    )

    assert first.text.startswith("Я помощник.")
    assert "Уточните дату" in second.text
    assert "время 13:00" in third.text
    assert model_calls == 4
    assert memory.append.await_count == 3


def test_number_validation_allows_dialog_history_and_ignores_list_numbers():
    from pybot.services.agent.service import AgentService

    history = [
        ModelRequest(parts=[UserPromptPart("Мои посещения за сентябрь 2020")]),
        ModelResponse(parts=[TextPart("За сентябрь 2020 было 2 посещения.")]),
    ]
    messages: list[ModelMessage] = [ModelRequest(parts=[ToolReturnPart("read", "Итог за октябрь 2026: 4 посещения.")])]
    answer = "В прошлом ответе было 2 посещения за 2020 год.\n1. Октябрь 2026\n2) Всего 4 посещения."

    assert AgentService._validate_numbers(answer, messages, "Повтори итог", history) == answer


def test_number_guard_allows_only_dates_resolved_from_an_explicit_relative_day():
    from pybot.services.agent.service import AgentService

    question = "Создай встречу завтра в 13:00\n\nТекущая дата (Екатеринбург): 2026-10-16."
    grounded_answer = "Подготовлю встречу на 17.10.2026 в 13:00."
    unsupported_answer = "Подготовлю встречу на 18.10.2026 в 13:00."

    assert AgentService._validate_numbers(grounded_answer, [], question) == grounded_answer
    with pytest.raises(ValueError, match="numbers absent from approved evidence"):
        AgentService._validate_numbers(unsupported_answer, [], question)


def test_number_guard_error_classifies_rejected_values_without_preserving_them():
    from pybot.services.agent.diagnostics import AgentNumbersGuardError
    from pybot.services.agent.service import AgentService

    with pytest.raises(AgentNumbersGuardError) as error:
        AgentService._validate_numbers(
            "Завтра 13:00 будет 991337 баллов.\n1. Уточнение",
            [],
            "Что дальше?",
        )

    assert error.value.rejected_count == 3
    assert error.value.number_categories == {
        "list_item_number": 1,
        "time_or_date": 2,
        "dialog_history": 0,
        "other": 1,
    }
    assert "991337" not in repr(error.value)


@pytest.mark.asyncio
async def test_model_timeout_cancels_run(agent_settings, conversation):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        return ModelResponse(parts=[TextPart("never")])

    agent_settings.agent_timeout_s = 1
    service, _, memory = build_service(agent_settings, model=FunctionModel(slow_model))
    result = await service.answer(conversation, "help")
    assert result.status == AgentReplyStatus.UNAVAILABLE
    assert started.is_set() and cancelled.is_set()
    memory.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_max_steps_stops_tool_loop(agent_settings, conversation):
    requests = []

    def loop_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(messages)
        return ModelResponse(parts=[ToolCallPart("bot_capabilities", {}, tool_call_id=f"call-{len(requests)}")])

    agent_settings.agent_max_steps = 2
    service, _, _ = build_service(agent_settings, model=FunctionModel(loop_model))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_simultaneous_model_tools_run_sequentially(agent_settings, conversation):
    active_tools = 0
    maximum_active_tools = 0

    async def track_action() -> str:
        nonlocal active_tools, maximum_active_tools
        active_tools += 1
        maximum_active_tools = max(maximum_active_tools, active_tools)
        await asyncio.sleep(0.02)
        active_tools -= 1
        return "Готово"

    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Оба действия выполнены")])
        return ModelResponse(
            parts=[
                ToolCallPart("first_action", {}, tool_call_id="first"),
                ToolCallPart("second_action", {}, tool_call_id="second"),
            ]
        )

    registry = AgentToolRegistry(
        (
            _test_entry(track_action, name="first_action"),
            _test_entry(track_action, name="second_action"),
        )
    )
    service, _, _ = build_service(agent_settings, model=FunctionModel(model), registry=registry)

    result = await service.answer(conversation, "Выполни два чтения")

    assert result.text == "Оба действия выполнены"
    assert maximum_active_tools == 1


@pytest.mark.asyncio
async def test_fabricated_numbers_are_rejected(agent_settings, conversation):
    service, _, memory = build_service(
        agent_settings, model=TestModel(call_tools=["bot_capabilities"], custom_output_text="У вас 999 баллов")
    )
    assert (await service.answer(conversation, "сколько баллов")).status == AgentReplyStatus.UNAVAILABLE
    memory.append.assert_not_awaited()


def action_registry(executions, *, roles=frozenset({"Admin"})):
    async def change_data(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(roles)
        executions.append("changed")
        return "Действие выполнено"

    return AgentToolRegistry(
        (
            _test_entry(
                change_data,
                roles=roles,
                requires_confirmation=True,
                summary=lambda call, recipient: "Изменить тестовые данные",
            ),
        )
    )


def _test_entry(
    function,
    *,
    name=None,
    roles=frozenset({"Admin", "Mentor", "Student"}),
    requires_confirmation=False,
    summary=None,
):
    description = function.__doc__ or "Тестовый инструмент для проверки поведения агента."
    tool = Tool(function, name=name, description=description, requires_approval=requires_confirmation)
    metadata = AgentToolMetadata(
        name=tool.name,
        roles=roles,
        kind="action" if requires_confirmation else "read",
        requires_confirmation=requires_confirmation,
        description=description,
        service="TestAgentService",
    )
    return RegisteredAgentTool(tool, metadata, summary)


async def defer_action(settings, conversation, executions):
    registry = action_registry(executions)
    model = TestModel(call_tools=["change_data"], custom_output_text="Действие выполнено")
    service, users, memory = build_service(settings, registry=registry, model=model)
    users.find_all_user_roles.return_value = {"Admin"}
    result = await service.answer(conversation, "измени данные")
    assert result.confirmation_id is not None
    service.runtime.bind_message(result.confirmation_id, 55)
    return service, users, memory, result.confirmation_id


@pytest.mark.asyncio
async def test_approval_author_and_double_click(agent_settings, conversation):
    executions = []
    service, _, memory, confirmation = await defer_action(agent_settings, conversation, executions)
    assert executions == []
    memory.append.assert_not_awaited()
    foreign = conversation.model_copy(update={"telegram_id": 99})
    result = await service.confirm(confirmation, foreign, 55, approved=True)
    assert result.alert and result.status == AgentReplyStatus.NOT_AUTHOR
    first, second = await asyncio.gather(
        service.confirm(confirmation, conversation, 55, approved=True),
        service.confirm(confirmation, conversation, 55, approved=True),
    )
    assert first.text == "Действие выполнено"
    assert second.status == AgentReplyStatus.EXPIRED
    assert executions == ["changed"]
    memory.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_confirmation_executes_only_first_of_multiple_actions(agent_settings, conversation):
    executions = []
    model_calls = 0
    roles = frozenset({"Admin"})

    async def first_action(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(roles)
        executions.append("first")
        return "Первое действие выполнено"

    async def second_action(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(roles)
        executions.append("second")
        return "Второе действие выполнено"

    def summary(call, recipient):
        del recipient
        return "Предпросмотр первого действия" if call.tool_name == "first_action" else "Предпросмотр второго действия"

    registry = AgentToolRegistry(
        (
            _test_entry(first_action, roles=roles, requires_confirmation=True, summary=summary),
            _test_entry(second_action, roles=roles, requires_confirmation=True, summary=summary),
        )
    )

    def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            raise RuntimeError("The confirmed action must not depend on another model request")
        return ModelResponse(
            parts=[
                ToolCallPart("first_action", {}, tool_call_id="first"),
                ToolCallPart("second_action", {}, tool_call_id="second"),
            ]
        )

    service, users, _ = build_service(agent_settings, model=FunctionModel(model), registry=registry)
    users.find_all_user_roles.return_value = {"Admin"}
    pending = await service.answer(conversation, "Выполни два действия")

    assert pending.confirmation_id is not None
    assert "Предпросмотр первого действия" in pending.text
    assert "Предпросмотр второго действия" not in pending.text
    assert (
        "Выполняю по одному действию за раз. Подтвердите это, остальное напишите отдельным сообщением" in pending.text
    )
    assert len(service.runtime.pending[pending.confirmation_id].requests.approvals) == 2
    service.runtime.bind_message(pending.confirmation_id, 55)

    confirmed = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)
    repeated = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert confirmed.text == "Первое действие выполнено"
    assert repeated.status == AgentReplyStatus.EXPIRED
    assert model_calls == 1
    assert executions == ["first"]


def test_approval_results_deny_every_action_after_the_first():
    from pybot.services.agent.service import AgentService

    results = AgentService._approval_results(
        [
            ToolCallPart("first_action", {}, tool_call_id="first"),
            ToolCallPart("second_action", {}, tool_call_id="second"),
        ]
    )

    assert results.approvals["first"] is True
    assert isinstance(results.approvals["second"], ToolDenied)
    assert results.approvals["second"].message == "Подтверждается только первое действие."


@pytest.mark.asyncio
async def test_slow_confirmed_action_is_not_limited_by_model_timeout(agent_settings, conversation):
    executions = []
    roles = frozenset({"Admin"})
    agent_settings.agent_timeout_s = 0.5

    async def slow_action(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(roles)
        await asyncio.sleep(agent_settings.agent_timeout_s + 0.05)
        executions.append("completed")
        return "Медленное действие завершено"

    registry = AgentToolRegistry(
        (
            _test_entry(
                slow_action,
                roles=roles,
                requires_confirmation=True,
                summary=lambda call, recipient: "Подтвердить",
            ),
        )
    )
    service, users, _ = build_service(
        agent_settings,
        model=TestModel(call_tools=["slow_action"]),
        registry=registry,
    )
    users.find_all_user_roles.return_value = {"Admin"}

    pending = await service.answer(conversation, "Выполни медленное действие")
    assert pending.confirmation_id is not None
    service.runtime.bind_message(pending.confirmation_id, 55)

    result = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert result.text == "Медленное действие завершено"
    assert executions == ["completed"]


@pytest.mark.asyncio
async def test_action_result_survives_memory_failure_without_followup_model_request(agent_settings, conversation):
    model_calls = 0
    executions = []

    def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            raise RuntimeError("model failed after action")
        return ModelResponse(parts=[ToolCallPart("change_data", {}, tool_call_id="change-data")])

    async def change_data(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(frozenset({"Admin"}))
        executions.append("completed")
        return "Изменение выполнено"

    registry = AgentToolRegistry(
        (
            _test_entry(
                change_data,
                roles=frozenset({"Admin"}),
                requires_confirmation=True,
                summary=lambda call, recipient: "Подтвердить",
            ),
        )
    )
    service, users, memory = build_service(agent_settings, model=FunctionModel(model), registry=registry)
    users.find_all_user_roles.return_value = {"Admin"}
    memory.append.side_effect = RuntimeError("memory unavailable")

    pending = await service.answer(conversation, "Измени данные")
    assert pending.confirmation_id is not None
    service.runtime.bind_message(pending.confirmation_id, 55)

    result = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert result.status == AgentReplyStatus.SUCCESS
    assert result.text == "Изменение выполнено"
    assert executions == ["completed"]
    assert model_calls == 1
    memory.append.assert_awaited_once_with(conversation, "Измени данные", "Изменение выполнено")


@pytest.mark.asyncio
async def test_role_request_is_preflighted_then_created_only_after_approval(agent_settings, conversation):
    from pydantic_ai.models.test import TestModel
    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)

    service, users, memory = build_service(
        agent_settings,
        model=TestModel(call_tools=["request_mentor_role"], custom_output_text="Заявка отправлена"),
        actions=actions,
    )
    users.find_all_user_roles.return_value = {"Student"}

    reply = await service.answer(conversation, "Подай заявку на роль наставника")

    assert reply.confirmation_id is not None
    assert reply.text.startswith("Подать от вашего имени заявку на роль Mentor")
    actions.validate_role_request.assert_awaited_once_with(42)
    actions.request_mentor_role.assert_not_awaited()
    memory.append.assert_not_awaited()

    service.runtime.bind_message(reply.confirmation_id, 55)
    confirmed = await service.confirm(reply.confirmation_id, conversation, 55, approved=True)

    assert confirmed.text == "Заявка на роль Mentor отправлена администраторам."
    actions.request_mentor_role.assert_awaited_once_with(42)
    assert actions.validate_role_request.await_count >= 2
    memory.append.assert_awaited_once_with(
        conversation,
        "Подай заявку на роль наставника",
        "Заявка на роль Mentor отправлена администраторам.",
    )


@pytest.mark.asyncio
async def test_ineligible_role_request_does_not_reach_confirmation(agent_settings, conversation):
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from pybot.domain.exceptions import RoleRequestNotAllowedError
    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)

    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Заявку сейчас подать нельзя")])
        return ModelResponse(parts=[ToolCallPart("request_mentor_role", {}, tool_call_id="request-role")])

    service, users, _ = build_service(agent_settings, model=FunctionModel(model), actions=actions)
    users.find_all_user_roles.return_value = {"Student"}
    actions.validate_role_request.side_effect = RoleRequestNotAllowedError()

    reply = await service.answer(conversation, "Подай заявку на роль наставника")

    assert reply.confirmation_id is None
    assert reply.text == "Заявку сейчас подать нельзя"
    actions.request_mentor_role.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "expired", "restart", "wrong_message", "wrong_topic"])
async def test_cancel_stale_and_bound_context(agent_settings, conversation, mode):
    executions = []
    service, _, _, confirmation = await defer_action(agent_settings, conversation, executions)
    message_id = 55
    if mode == "expired":
        service.runtime.pending[confirmation].expires_at = 0
    if mode == "restart":
        service.runtime.pending.clear()
    if mode == "wrong_message":
        message_id = 56
    if mode == "wrong_topic":
        conversation = conversation.model_copy(update={"topic_id": 4})
    result = await service.confirm(confirmation, conversation, message_id, approved=mode != "cancel")
    assert result.status == (AgentReplyStatus.CANCELLED if mode == "cancel" else AgentReplyStatus.EXPIRED)
    assert executions == []


@pytest.mark.asyncio
async def test_revoked_role_at_approval_never_executes(agent_settings, conversation):
    executions = []
    service, users, _, confirmation = await defer_action(agent_settings, conversation, executions)
    # Discovery at resume still sees Admin; actual execution must check fresh roles.
    users.find_all_user_roles.side_effect = [{"Admin"}, {"Admin"}, {"Student"}]
    result = await service.confirm(confirmation, conversation, 55, approved=True)
    assert result.status == AgentReplyStatus.ACCESS_DENIED
    assert executions == []


@pytest.mark.asyncio
async def test_tools_filtered_for_student_and_forged_call_not_executed(agent_settings, conversation):
    executions = []
    registry = action_registry(executions)
    seen_tools = []

    def malicious_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen_tools.append([tool.name for tool in info.function_tools])
        return ModelResponse(parts=[ToolCallPart("change_data", {}, tool_call_id="forged")])

    service, _, _ = build_service(agent_settings, registry=registry, model=FunctionModel(malicious_model))
    assert (await service.answer(conversation, "измени чужие данные")).status == AgentReplyStatus.UNAVAILABLE
    assert all(tools == [] for tools in seen_tools)
    assert executions == []


@pytest.mark.asyncio
async def test_internal_tool_dispatch_rejects_unknown_and_unauthorized(agent_settings, conversation):
    executions = []
    registry = action_registry(executions)
    service, users, _ = build_service(agent_settings)
    deps = AgentDependencies(conversation, users, agent_settings, service.reads, service.actions)
    from pybot.domain.agent import AgentAccessDeniedError

    ctx = Mock(deps=deps)
    toolset = registry.for_roles(frozenset({"Admin"}))
    with pytest.raises(AgentAccessDeniedError):
        await toolset.call_tool("forged", {}, ctx, Mock())
    with pytest.raises(AgentAccessDeniedError):
        await toolset.call_tool("change_data", {}, ctx, Mock())
    assert executions == []


@pytest.mark.asyncio
async def test_capabilities_admin_and_broadcast_policy(agent_settings, conversation):
    from pybot.services.agent.tools import bot_capabilities

    service, users, _ = build_service(agent_settings)
    deps = AgentDependencies(conversation, users, agent_settings, service.reads, service.actions)
    ctx = Mock(deps=deps)
    student = await bot_capabilities(ctx)
    assert "/academic_points" not in student and "/broadcast" not in student
    users.find_all_user_roles.return_value = {"Admin"}
    admin = await bot_capabilities(ctx)
    assert "/academic_points" in admin and "/broadcast" in admin
    agent_settings.broadcast_allowed_roles = {"Mentor"}
    admin = await bot_capabilities(ctx)
    assert "/broadcast" not in admin


@pytest.mark.asyncio
async def test_revoked_role_before_resume_returns_denial(agent_settings, conversation):
    executions = []
    service, users, _, confirmation = await defer_action(agent_settings, conversation, executions)
    users.find_all_user_roles.return_value = {"Student"}
    assert (
        await service.confirm(confirmation, conversation, 55, approved=True)
    ).status == AgentReplyStatus.ACCESS_DENIED
    assert executions == []


@pytest.mark.asyncio
async def test_removed_user_at_approval_is_asked_to_register(agent_settings, conversation):
    executions = []
    service, users, _, confirmation = await defer_action(agent_settings, conversation, executions)
    users.find_user_by_telegram_id.return_value = None
    assert (
        await service.confirm(confirmation, conversation, 55, approved=True)
    ).status == AgentReplyStatus.REGISTRATION_REQUIRED
    assert executions == []


@pytest.mark.asyncio
async def test_text_memory_replayed_without_tool_results(agent_settings, conversation):
    from pybot.dto.agent import AgentMemoryMessage
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    seen = []

    def replay_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        return ModelResponse(parts=[TextPart("Откройте меню")])

    service, _, memory = build_service(agent_settings, model=FunctionModel(replay_model))
    memory.load.return_value = [
        AgentMemoryMessage(role="user", text="прошлый вопрос"),
        AgentMemoryMessage(role="assistant", text="прошлый ответ"),
    ]
    result = await service.answer(conversation, "новый вопрос")
    assert result.text == "Откройте меню"
    assert isinstance(seen[0], ModelRequest)
    assert isinstance(seen[0].parts[0], UserPromptPart)
    assert seen[0].parts[0].content == "прошлый вопрос"
    assert isinstance(seen[1], ModelResponse)
    assert isinstance(seen[1].parts[0], TextPart)
    assert seen[1].parts[0].content == "прошлый ответ"
    assert isinstance(seen[2], ModelRequest)
    assert isinstance(seen[2].parts[0], UserPromptPart)
    assert seen[2].parts[0].content.startswith("новый вопрос\n\nТекущая дата (Екатеринбург): ")


def test_approval_summary_mandatory_and_duplicate_tools_rejected():
    async def action(ctx: RunContext[AgentDependencies]) -> str:
        return "test"

    with pytest.raises(ValueError, match="confirmation summary"):
        AgentToolRegistry((_test_entry(action, requires_confirmation=True),))
    with pytest.raises(ValueError, match="Duplicate"):
        AgentToolRegistry((_test_entry(action), _test_entry(action)))


@pytest.mark.parametrize(
    ("answer", "evidence"),
    [
        ("9 минут; 1,50 балла", "09 минут; 1.5 балла"),
        ("-5 баллов", "−5 баллов"),
        ("2020-09-09", "2020-09-09"),
        ("07.09.2020", "2020-09-07"),
        (
            "В октябре 2026 у вас было 0 посещений.",
            "Мои посещения за 2026-10: 0; подтверждённых записей нет.",
        ),
    ],
)
def test_number_validation_accepts_equivalent_formats(answer, evidence):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from pybot.services.agent.service import AgentService

    assert AgentService._validate_numbers(answer, [ModelRequest(parts=[ToolReturnPart("read", evidence)])]) == answer


@pytest.mark.parametrize("answer", ["5 баллов", "999 баллов"])
def test_number_validation_preserves_negative_sign_and_rejects_inventions(answer):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from pybot.services.agent.service import AgentService

    with pytest.raises(ValueError):
        AgentService._validate_numbers(answer, [ModelRequest(parts=[ToolReturnPart("read", "-5 баллов")])])


def test_number_validation_allows_numbers_from_the_current_question_only():
    from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

    from pybot.services.agent.service import AgentService

    evidence: list[ModelMessage] = [ModelRequest(parts=[ToolReturnPart("read", "Баллы: 75")])]
    profile_answer = "Профиль 771234: 75 баллов"
    broadcast_answer = "Встреча в 14:00"

    assert AgentService._validate_numbers(profile_answer, evidence, "Покажи профиль 771234") == profile_answer
    assert AgentService._validate_numbers(broadcast_answer, evidence, "Рассылка: встреча в 14:00") == broadcast_answer
    with pytest.raises(ValueError):
        AgentService._validate_numbers("У пользователя 999 баллов", evidence, "Покажи профиль 771234")
