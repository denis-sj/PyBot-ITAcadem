from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from pybot.domain.services.technology_stack import TechnologyLevel
from pybot.dto.agent import AgentReplyStatus
from pybot.services.agent.reads import AgentReadService
from pybot.services.agent.tools import AgentToolRegistry
from pybot.services.ports.agent import AgentActionsPort
from tests.agent.conftest import build_service


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "validator_name", "executor_name", "expected_arguments", "preview"),
    [
        (
            "set_my_technology",
            {"technology": "Docker", "level": "learning"},
            "validate_my_stack_item",
            "set_my_stack_item",
            (42, "Docker", TechnologyLevel.LEARNING),
            "добавить или изменить",
        ),
        (
            "remove_my_technology",
            {"technology": "питон"},
            "validate_my_stack_item_removal",
            "remove_my_stack_item",
            (42, "питон"),
            "удалить",
        ),
    ],
)
@pytest.mark.asyncio
async def test_stack_actions_are_confirmed_and_always_target_the_authenticated_actor(
    agent_settings,
    conversation,
    tool_name,
    tool_args,
    validator_name,
    executor_name,
    expected_arguments,
    preview,
) -> None:
    actions = Mock(spec=AgentActionsPort)
    getattr(actions, validator_name).return_value = None
    getattr(actions, executor_name).return_value = "Действие над собственным стеком выполнено."

    model = FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart(tool_name, tool_args, tool_call_id="stack-change")])
    )
    service, users, memory = build_service(agent_settings, model=model, actions=actions)
    users.find_all_user_roles.return_value = {"Student"}

    pending = await service.answer(conversation, "Измени мой стек")

    assert pending.status == AgentReplyStatus.SUCCESS
    assert pending.confirmation_id is not None
    assert preview in pending.text.casefold()
    getattr(actions, validator_name).assert_awaited_once_with(*expected_arguments)
    getattr(actions, executor_name).assert_not_awaited()
    memory.append.assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 55)
    confirmed = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert confirmed.text == "Действие над собственным стеком выполнено."
    getattr(actions, executor_name).assert_awaited_once_with(*expected_arguments)
    memory.append.assert_awaited_once_with(conversation, "Измени мой стек", confirmed.text)


def test_stack_actions_are_available_to_registered_users_and_have_no_recipient_argument() -> None:
    registry = AgentToolRegistry()
    student_tools = registry.for_roles(frozenset({"Student"}))
    admin_tools = registry.for_roles(frozenset({"Admin"}))

    for name in ("set_my_technology", "remove_my_technology"):
        entry = next(entry for entry in registry.entries if entry.tool.name == name)
        assert entry.tool.requires_approval is True
        assert name in student_tools.policies
        assert name in admin_tools.policies
        properties = entry.tool.function_schema.json_schema["properties"]
        expected_arguments = {"technology", "level"} if name == "set_my_technology" else {"technology"}
        assert properties.keys() == expected_arguments


def test_goal_actions_are_available_to_registered_users_and_require_confirmation() -> None:
    registry = AgentToolRegistry()
    for name in ("set_my_goal", "mark_my_goal_achieved"):
        entry = next(entry for entry in registry.entries if entry.tool.name == name)
        assert entry.tool.requires_approval is True
        assert entry.allowed_roles == frozenset()
        assert name in registry.for_roles(frozenset({"Student"})).policies
        assert name in registry.for_roles(frozenset({"Admin"})).policies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "tool_name",
        "tool_args",
        "validator",
        "executor",
        "expected_validator_args",
        "expected_executor_args",
        "preview",
    ),
    [
        (
            "set_my_goal",
            {"text": "  пройти   собеседование "},
            "validate_my_goal",
            "set_my_goal",
            ("  пройти   собеседование ",),
            (42, "  пройти   собеседование "),
            "пройти собеседование",
        ),
        (
            "mark_my_goal_achieved",
            {},
            "validate_my_goal_completion",
            "complete_my_goal",
            (42,),
            (42,),
            "достигнутую",
        ),
    ],
)
async def test_goal_actions_require_confirmation_and_target_the_authenticated_actor(
    agent_settings,
    conversation,
    tool_name,
    tool_args,
    validator,
    executor,
    expected_validator_args,
    expected_executor_args,
    preview,
) -> None:
    actions = Mock(spec=AgentActionsPort)
    getattr(actions, validator).return_value = None
    getattr(actions, executor).return_value = "Цель обновлена от имени участника."
    model = FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart(tool_name, tool_args, tool_call_id="goal-action")])
    )
    service, users, memory = build_service(agent_settings, model=model, actions=actions)
    users.find_all_user_roles.return_value = {"Student"}

    pending = await service.answer(conversation, "Обнови мою цель")

    assert pending.status == AgentReplyStatus.SUCCESS
    assert pending.confirmation_id is not None
    assert preview in pending.text.casefold()
    getattr(actions, validator).assert_awaited_once_with(*expected_validator_args)
    getattr(actions, executor).assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 56)
    result = await service.confirm(pending.confirmation_id, conversation, 56, approved=True)

    assert result.text == "Цель обновлена от имени участника."
    getattr(actions, executor).assert_awaited_once_with(*expected_executor_args)
    memory.append.assert_awaited_once_with(conversation, "Обнови мою цель", result.text)


@pytest.mark.asyncio
async def test_stack_read_tools_are_public_to_registered_actor(agent_settings, conversation) -> None:
    from pydantic_ai.models.test import TestModel

    reads = Mock(spec=AgentReadService)
    reads.get_my_stack = AsyncMock(return_value="Твой стек: Python")
    service, users, _ = build_service(
        agent_settings,
        model=TestModel(call_tools=["get_my_technology_stack"], custom_output_text="Твой стек: Python"),
        reads=reads,
    )
    users.find_all_user_roles.return_value = {"Student"}

    reply = await service.answer(conversation, "Покажи мой стек")

    assert reply.text == "Твой стек: Python"
    reads.get_my_stack.assert_awaited_once_with(42)
