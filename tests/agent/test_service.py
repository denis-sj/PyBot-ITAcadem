import asyncio
from unittest.mock import Mock

import pytest
from pydantic_ai import RunContext, Tool, ToolDenied
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
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
            RegisteredAgentTool(Tool(track_action, name="first_action")),
            RegisteredAgentTool(Tool(track_action, name="second_action")),
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

    tool = Tool(change_data, requires_approval=True)
    return AgentToolRegistry((RegisteredAgentTool(tool, roles, lambda call, recipient: "Изменить тестовые данные"),))


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
            RegisteredAgentTool(Tool(first_action, requires_approval=True), roles, summary),
            RegisteredAgentTool(Tool(second_action, requires_approval=True), roles, summary),
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
        (RegisteredAgentTool(Tool(slow_action, requires_approval=True), roles, lambda call, recipient: "Подтвердить"),)
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
            RegisteredAgentTool(
                Tool(change_data, requires_approval=True),
                frozenset({"Admin"}),
                lambda call, recipient: "Подтвердить",
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
    assert seen[2].parts[0].content == "новый вопрос"


def test_approval_summary_mandatory_and_duplicate_tools_rejected():
    async def action(ctx: RunContext[AgentDependencies]) -> str:
        return "test"

    with pytest.raises(ValueError, match="confirmation summary"):
        AgentToolRegistry((RegisteredAgentTool(Tool(action, requires_approval=True)),))
    with pytest.raises(ValueError, match="Duplicate"):
        AgentToolRegistry((RegisteredAgentTool(Tool(action)), RegisteredAgentTool(Tool(action))))


@pytest.mark.parametrize(
    ("answer", "evidence"),
    [("9 минут; 1,50 балла", "09 минут; 1.5 балла"), ("-5 баллов", "−5 баллов"), ("2020-09-09", "2020-09-09")],
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
