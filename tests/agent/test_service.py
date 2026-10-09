import asyncio
from unittest.mock import Mock

import pytest
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pybot.dto.agent import AgentReplyStatus
from pybot.services.agent.tools import AgentDependencies, AgentToolRegistry, RegisteredAgentTool
from tests.agent.conftest import build_service


@pytest.mark.asyncio
async def test_capabilities_use_service_roles_and_persist_only_final_text(agent_settings, conversation):
    service, users, memory = build_service(agent_settings)
    result = await service.answer(conversation, "Что умеет бот?")
    assert result.text == "Откройте /profile или /visits"
    assert result.confirmation_id is None
    users.find_all_user_roles.assert_awaited()
    memory.append.assert_awaited_once_with(conversation, "Что умеет бот?", result.text)


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
@pytest.mark.parametrize("error", [TimeoutError("secret detail"), RuntimeError("secret detail")])
async def test_provider_failure_is_safe(agent_settings, conversation, error, monkeypatch):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(service.model, "get_model", Mock(side_effect=error))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    memory.append.assert_not_awaited()


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
async def test_fabricated_numbers_are_rejected(agent_settings, conversation):
    service, _, memory = build_service(agent_settings, model=TestModel(custom_output_text="У вас 999 баллов"))
    assert (await service.answer(conversation, "сколько баллов")).status == AgentReplyStatus.UNAVAILABLE
    memory.append.assert_not_awaited()


def action_registry(executions, *, roles=frozenset({"Admin"})):
    async def change_data(ctx: RunContext[AgentDependencies]) -> str:
        await ctx.deps.require_roles(roles)
        executions.append("changed")
        return "Действие выполнено"

    tool = Tool(change_data, requires_approval=True)
    return AgentToolRegistry((RegisteredAgentTool(tool, roles, lambda call: "Изменить тестовые данные"),))


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
    deps = AgentDependencies(conversation, users, agent_settings)
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
    deps = AgentDependencies(conversation, users, agent_settings)
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
    assert seen[2].parts[0].content == "новый вопрос"


def test_approval_summary_mandatory_and_duplicate_tools_rejected():
    async def action(ctx: RunContext[AgentDependencies]) -> str:
        return "test"

    with pytest.raises(ValueError, match="confirmation summary"):
        AgentToolRegistry((RegisteredAgentTool(Tool(action, requires_approval=True)),))
    with pytest.raises(ValueError, match="Duplicate"):
        AgentToolRegistry((RegisteredAgentTool(Tool(action)), RegisteredAgentTool(Tool(action))))
