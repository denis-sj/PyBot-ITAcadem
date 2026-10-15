"""Broadcast tools enforce configured roles and wait for explicit confirmation."""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from pybot.core.constants import RoleEnum
from pybot.domain.agent import AgentAccessDeniedError
from pybot.dto.agent import AgentConversation, AgentReplyStatus
from pybot.services.agent.broadcast_tools import broadcast_confirmation_summary
from pybot.services.agent.dependencies import AgentDependencies
from pybot.services.agent.tools import AgentToolRegistry
from tests.agent.conftest import build_service


async def broadcast_model(messages, info):
    if any(
        isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    ):
        return ModelResponse(parts=[TextPart("Рассылка завершена: отправлено 3 сообщений.")])
    return ModelResponse(
        parts=[
            ToolCallPart(
                "send_broadcast",
                {"audience": "role", "target": "Student", "message": "Собрание в 18:00"},
                tool_call_id="send-broadcast",
            )
        ]
    )


def test_broadcast_tool_uses_configured_roles_and_requires_approval():
    tool_name = "send_broadcast"
    empty_registry = AgentToolRegistry()
    configured_registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({RoleEnum.ADMIN.value}))

    assert tool_name not in empty_registry.for_roles(frozenset({RoleEnum.ADMIN.value})).policies
    with pytest.raises(AgentAccessDeniedError):
        empty_registry.get_policy(tool_name)
    assert tool_name not in configured_registry.for_roles(frozenset({RoleEnum.STUDENT.value})).policies
    assert tool_name in configured_registry.for_roles(frozenset({RoleEnum.ADMIN.value})).policies
    assert next(entry for entry in configured_registry.entries if entry.tool.name == tool_name).tool.requires_approval


@pytest.mark.parametrize(
    ("arguments", "audience_label"),
    [
        ({"audience": "all", "message": "Встреча"}, "всем зарегистрированным пользователям"),
        ({"audience": "role", "target": "Student", "message": "Встреча"}, "пользователям с ролью Student"),
        (
            {"audience": "competence", "target": "Python", "message": "Встреча"},
            "пользователям с компетенцией «Python»",
        ),
    ],
)
def test_broadcast_preview_identifies_audience_and_full_message(arguments, audience_label):
    call = ToolCallPart("send_broadcast", arguments, tool_call_id="broadcast-preview")

    preview = broadcast_confirmation_summary(call, None)

    assert audience_label in preview
    assert "---\nВстреча\n---" in preview


@pytest.mark.asyncio
async def test_broadcast_preview_is_exact_and_sending_waits_for_author_confirmation(agent_settings, conversation):
    from unittest.mock import AsyncMock, Mock

    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)
    actions.validate_broadcast = AsyncMock()
    actions.broadcast = AsyncMock(return_value="Рассылка выполнена: получателей 3, доставлено 3.")
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({RoleEnum.ADMIN.value}))
    private_conversation = AgentConversation(chat_id=11, topic_id=None, telegram_id=11)
    service, users, _ = build_service(
        agent_settings,
        model=FunctionModel(broadcast_model),
        registry=registry,
        actions=actions,
    )
    users.find_all_user_roles.return_value = {RoleEnum.ADMIN.value}

    pending = await service.answer(private_conversation, "Подготовь рассылку студентам")

    assert pending.confirmation_id is not None
    assert "пользователям с ролью Student" in pending.text
    assert "---\nСобрание в 18:00\n---" in pending.text
    assert "Собрание в 18:00" in pending.text
    actions.validate_broadcast.assert_awaited_once_with("role", "Student", "Собрание в 18:00")
    actions.broadcast.assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 99)
    result = await service.confirm(pending.confirmation_id, private_conversation, 99, approved=True)

    assert result.status == AgentReplyStatus.SUCCESS
    assert result.text == "Рассылка выполнена: получателей 3, доставлено 3."
    actions.broadcast.assert_awaited_once_with("role", "Student", "Собрание в 18:00")


@pytest.mark.asyncio
async def test_group_context_cannot_prepare_a_broadcast(agent_settings, conversation):
    from unittest.mock import AsyncMock, Mock

    from pydantic_ai.exceptions import ModelRetry

    from pybot.services.agent.broadcast_tools import validate_broadcast
    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)
    actions.validate_broadcast = AsyncMock()
    service, users, _ = build_service(agent_settings, actions=actions)
    users.find_all_user_roles.return_value = {RoleEnum.ADMIN.value}
    deps = AgentDependencies(conversation, users, agent_settings, service.reads, actions)

    with pytest.raises(ModelRetry, match="только в личном чате"):
        await validate_broadcast(Mock(deps=deps), "Собрание в 18:00", "all")
    actions.validate_broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_without_audience_asks_for_clarification_before_confirmation(agent_settings):
    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Кому отправить: всем, по роли или по компетенции?")])
        return ModelResponse(
            parts=[ToolCallPart("send_broadcast", {"message": "Собрание в 18:00"}, tool_call_id="missing-audience")]
        )

    from unittest.mock import AsyncMock, Mock

    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)
    actions.validate_broadcast = AsyncMock()
    actions.broadcast = AsyncMock()
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({RoleEnum.ADMIN.value}))
    conversation = AgentConversation(chat_id=11, topic_id=None, telegram_id=11)
    service, users, _ = build_service(agent_settings, model=FunctionModel(model), registry=registry, actions=actions)
    users.find_all_user_roles.return_value = {RoleEnum.ADMIN.value}

    result = await service.answer(conversation, "Отправь сообщение о собрании")

    assert result.confirmation_id is None
    assert result.text == "Кому отправить: всем, по роли или по компетенции?"
    assert not service.runtime.pending
    actions.validate_broadcast.assert_not_awaited()
    actions.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_student_cannot_dispatch_a_forged_broadcast_tool(agent_settings, conversation):
    from unittest.mock import AsyncMock, Mock

    from pybot.services.ports.agent import AgentActionsPort

    actions = Mock(spec=AgentActionsPort)
    actions.validate_broadcast = AsyncMock()
    actions.broadcast = AsyncMock()
    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({RoleEnum.ADMIN.value}))
    service, users, _ = build_service(agent_settings, registry=registry, actions=actions)
    dependencies = AgentDependencies(conversation, users, agent_settings, service.reads, actions)
    toolset = registry.for_roles(frozenset({RoleEnum.STUDENT.value}))

    with pytest.raises(AgentAccessDeniedError):
        await toolset.call_tool("send_broadcast", {}, Mock(deps=dependencies), Mock())

    actions.validate_broadcast.assert_not_awaited()
    actions.broadcast.assert_not_awaited()
