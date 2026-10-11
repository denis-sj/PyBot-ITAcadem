from unittest.mock import Mock
from pybot.services.agent.reads import AgentReadService
import pytest

from pybot.services.agent.tools import AgentToolRegistry

READ_TOOLS = {
    "get_my_profile",
    "get_my_attendance",
    "get_my_visits",
    "get_attendance_week_top",
    "get_attendance_month_top",
    "get_points_leaderboard",
    "get_roles_catalog",
    "get_competences_catalog",
    "get_my_roles",
    "get_my_competences",
    "get_bot_info",
}
ADMIN_READ_TOOLS = {
    "get_current_month",
    "get_club_attendance_report",
    "get_user_profile",
    "get_user_attendance",
    "get_current_chat_id",
}


def test_registered_user_sees_stage_one_read_tools_without_approval():
    registry = AgentToolRegistry()
    assert set(registry.for_roles(frozenset({"Student"})).policies) == READ_TOOLS | {"bot_capabilities"}
    assert ADMIN_READ_TOOLS.isdisjoint(registry.for_roles(frozenset({"Student"})).policies)
    assert ADMIN_READ_TOOLS <= registry.for_roles(frozenset({"Admin"})).policies.keys()
    assert all(not entry.tool.requires_approval for entry in registry.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "method_name", "arguments"),
    [
        ("get_my_profile", "get_profile", "actor"),
        ("get_my_attendance", "get_attendance_summary", "id"),
        ("get_my_visits", "get_visits", "period"),
        ("get_attendance_week_top", "get_week_top", "id"),
        ("get_attendance_month_top", "get_month_top", "period"),
        ("get_points_leaderboard", "get_points_leaderboard", "none"),
        ("get_roles_catalog", "get_roles_catalog", "none"),
        ("get_competences_catalog", "get_competences_catalog", "none"),
        ("get_my_roles", "get_user_roles", "id"),
        ("get_my_competences", "get_user_competences", "id"),
    ],
)
async def test_model_dispatches_reads_for_authenticated_actor(
    agent_settings, conversation, tool_name, method_name, arguments
):
    from pydantic_ai.models.test import TestModel

    from pybot.dto.agent import AgentReplyStatus

    from tests.agent.conftest import build_service

    service, users, memory = build_service(
        agent_settings, model=TestModel(call_tools=[tool_name], custom_output_text="Прочитано: 42")
    )
    method = getattr(service.reads, method_name)
    method.return_value = "Данные: 42"
    actor = users.find_user_by_telegram_id.return_value
    reply = await service.answer(conversation, "Мои данные")
    assert reply.status == AgentReplyStatus.SUCCESS
    assert reply.confirmation_id is None
    if arguments == "actor":
        method.assert_awaited_once_with(actor)
    elif arguments == "id":
        method.assert_awaited_once_with(42)
    elif arguments == "period":
        method.assert_awaited_once_with(42, None)
    else:
        method.assert_awaited_once_with()
    memory.append.assert_awaited_once_with(conversation, "Мои данные", "Прочитано: 42")


@pytest.mark.asyncio
async def test_info_uses_canonical_text(agent_settings, conversation):
    from pydantic_ai.models.test import TestModel

    from pybot.presentation.texts.texts import INFO_GLOBAL

    from tests.agent.conftest import build_service

    service, _, _ = build_service(
        agent_settings, model=TestModel(call_tools=["get_bot_info"], custom_output_text=INFO_GLOBAL)
    )
    assert (await service.answer(conversation, "Что это за бот?")).text == INFO_GLOBAL


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(READ_TOOLS))
async def test_each_read_rechecks_registration_before_service_call(agent_settings, conversation, tool_name):
    from unittest.mock import Mock

    from pybot.domain.agent import AgentAccessDeniedError
    from pybot.services.agent import read_tools
    from pybot.services.agent.tools import AgentDependencies

    from tests.agent.conftest import build_service

    reads = Mock(spec=AgentReadService)
    service, users, _ = build_service(agent_settings, reads=reads)
    users.find_user_by_telegram_id.return_value = None
    ctx = Mock(deps=AgentDependencies(conversation, users, agent_settings, service.reads))
    with pytest.raises(AgentAccessDeniedError):
        await getattr(read_tools, tool_name)(ctx)
    assert reads.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["get_my_profile", "get_my_visits", "get_my_roles", "get_my_competences"])
async def test_model_cannot_supply_another_user_id(agent_settings, conversation, tool_name):
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from tests.agent.conftest import build_service

    def forged_model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Чужие данные недоступны")])
        return ModelResponse(parts=[ToolCallPart(tool_name, {"user_id": 999}, tool_call_id="forged")])

    reads = Mock(spec=AgentReadService)
    service, _, _ = build_service(agent_settings, model=FunctionModel(forged_model), reads=reads)
    assert (await service.answer(conversation, "Данные другого пользователя")).text == "Чужие данные недоступны"
    assert reads.mock_calls == []


@pytest.mark.asyncio
async def test_removed_actor_after_discovery_cannot_read(agent_settings, conversation):
    from pydantic_ai.models.test import TestModel

    from pybot.dto.agent import AgentReplyStatus

    from tests.agent.conftest import build_service

    reads = Mock(spec=AgentReadService)
    service, users, memory = build_service(
        agent_settings, model=TestModel(call_tools=["get_my_profile"], custom_output_text="Мой профиль"), reads=reads
    )
    actor = users.find_user_by_telegram_id.return_value
    users.find_user_by_telegram_id.side_effect = [actor, actor, None]
    assert (await service.answer(conversation, "Мой профиль")).status == AgentReplyStatus.ACCESS_DENIED
    reads.get_profile.assert_not_awaited()
    memory.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_domain_error_does_not_leak_into_reply_or_memory(agent_settings, conversation):
    from pydantic_ai.models.test import TestModel

    from pybot.domain.exceptions import LevelNotFoundError
    from pybot.dto.agent import AgentReplyStatus

    from tests.agent.conftest import build_service

    reads = Mock(spec=AgentReadService)
    service, _, memory = build_service(agent_settings, model=TestModel(call_tools=["get_my_profile"]), reads=reads)
    reads.get_profile.side_effect = LevelNotFoundError(42)
    reply = await service.answer(conversation, "Мой профиль")
    assert reply.status == AgentReplyStatus.UNAVAILABLE
    assert reply.text == ""
    memory.append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,method_name", [("get_my_visits", "get_visits"), ("get_attendance_month_top", "get_month_top")]
)
async def test_explicit_month_is_validated_and_forwarded(agent_settings, conversation, tool_name, method_name):
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    from pybot.dto.agent_read import AgentMonth
    from tests.agent.conftest import build_service

    def month_model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Посещений: 2")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name, {"period": {"year": 2020, "month": 9}}, tool_call_id="month")]
        )

    reads = Mock(spec=AgentReadService)
    getattr(reads, method_name).return_value = "Посещений: 2"
    service, _, _ = build_service(agent_settings, reads=reads, model=FunctionModel(month_model))
    assert (await service.answer(conversation, "За сентябрь 2020")).text == "Посещений: 2"
    getattr(reads, method_name).assert_awaited_once_with(42, AgentMonth(year=2020, month=9))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period", [{"year": 2020, "month": 13}, {"year": 9999, "month": 1}, {"year": 2020, "month": 9, "user_id": 999}]
)
async def test_invalid_model_month_never_reaches_attendance_service(agent_settings, conversation, period):
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from tests.agent.conftest import build_service

    def month_model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Уточните месяц")])
        return ModelResponse(parts=[ToolCallPart("get_my_visits", {"period": period}, tool_call_id="month")])

    reads = Mock(spec=AgentReadService)
    service, _, _ = build_service(agent_settings, reads=reads, model=FunctionModel(month_model))
    assert (await service.answer(conversation, "Мои посещения")).text == "Уточните месяц"
    reads.get_visits.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_question_through_telegram_handler_is_immediate_reply(agent_settings, monkeypatch):
    from unittest.mock import AsyncMock

    from aiogram.types import Message
    from pydantic_ai.models.test import TestModel

    from pybot.presentation.bot.handlers.agent.handlers import handle_agent_message
    from tests.agent.conftest import build_message, build_service

    reads = Mock(spec=AgentReadService)
    reads.get_attendance_summary.return_value = "Посещений: 2"
    service, _, memory = build_service(
        agent_settings,
        reads=reads,
        model=TestModel(call_tools=["get_my_attendance"], custom_output_text="Посещений: 2"),
    )
    memory.mark_reply = AsyncMock()
    send = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))
    monkeypatch.setattr(Message, "answer", send)
    question = build_message("Сколько у меня посещений?")
    await handle_agent_message(question, service, service.runtime, memory)
    reads.get_attendance_summary.assert_awaited_once_with(42)
    assert send.await_args is not None
    assert send.await_args.args[0] == "Посещений: 2"
    assert send.await_args.kwargs["parse_mode"] is None
    assert send.await_args.kwargs["message_thread_id"] == 3
    assert send.await_args.kwargs["reply_parameters"].message_id == question.message_id
    assert send.await_args.kwargs.get("reply_markup") is None
    memory.mark_reply.assert_awaited_once_with(-100, 3, 50)
    assert service.runtime.pending == {}
