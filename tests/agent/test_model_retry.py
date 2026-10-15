"""Model retries share a deadline and never repeat confirmed application actions."""

from unittest.mock import Mock

import httpx2
import pytest
from pydantic_ai import RunContext, Tool
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from pybot.dto.agent import AgentReplyStatus
from pybot.services.agent import model_timeout as timeout_module
from pybot.services.agent.tools import AgentDependencies, AgentToolMetadata, AgentToolRegistry, RegisteredAgentTool
from tests.agent.conftest import build_service


@pytest.fixture
def immediate_retry(monkeypatch):
    monkeypatch.setattr(timeout_module.random, "uniform", lambda lower, upper: 0)


@pytest.mark.asyncio
async def test_model_succeeds_after_one_retry_and_logs_no_text(
    agent_settings, conversation, monkeypatch, immediate_retry
):
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelHTTPError(502, "synthetic-model", {"message": "private error content"})
        return ModelResponse(parts=[TextPart("Готово")])

    log = Mock()
    monkeypatch.setattr(timeout_module, "logger", log)
    service, _, _ = build_service(agent_settings, model=FunctionModel(model))
    reply = await service.answer(conversation, "private question")

    assert reply.status == AgentReplyStatus.SUCCESS
    assert attempts == 2
    assert log.warning.call_args.kwargs == {"attempt": 1, "status_code": 502}
    assert "event=agent_model_retry" in str(log.warning.call_args)
    assert "private" not in str(log.warning.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_retry_exhaustion_returns_safe_unavailable(agent_settings, conversation, status, immediate_retry):
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(status, "synthetic-model", {"message": "private detail"})

    service, _, memory = build_service(agent_settings, model=FunctionModel(model))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    assert attempts == 3
    memory.append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
async def test_client_errors_are_not_retried(agent_settings, conversation, status, immediate_retry):
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(status, "synthetic-model", {})

    service, _, _ = build_service(agent_settings, model=FunctionModel(model))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    assert attempts == 1


@pytest.mark.asyncio
async def test_connection_timeout_retries_model(agent_settings, conversation, immediate_retry):
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx2.ConnectTimeout("private timeout")
        return ModelResponse(parts=[TextPart("Готово")])

    service, _, _ = build_service(agent_settings, model=FunctionModel(model))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.SUCCESS
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_pause_shares_request_timeout(agent_settings, conversation, monkeypatch):
    attempts = 0

    def model(messages, info):
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(502, "synthetic-model", {})

    monkeypatch.setattr(timeout_module.random, "uniform", lambda lower, upper: 1)
    agent_settings.agent_timeout_s = 0.01
    service, _, _ = build_service(agent_settings, model=FunctionModel(model))
    assert (await service.answer(conversation, "help")).status == AgentReplyStatus.UNAVAILABLE
    assert attempts == 1


@pytest.mark.asyncio
async def test_confirmed_action_transient_error_is_never_retried(agent_settings, conversation, immediate_retry):
    executions = 0

    async def failing_action(ctx: RunContext[AgentDependencies]) -> str:
        nonlocal executions
        await ctx.deps.require_roles(frozenset({"Admin"}))
        executions += 1
        raise ModelHTTPError(502, "synthetic-model", {})

    def model(messages, info):
        return ModelResponse(parts=[ToolCallPart("failing_action", {}, tool_call_id="synthetic-action")])

    tool = Tool(
        failing_action,
        description="Выполнить тестовое действие после подтверждения.",
        requires_approval=True,
    )
    registry = AgentToolRegistry(
        (
            RegisteredAgentTool(
                tool,
                AgentToolMetadata(
                    name=tool.name,
                    roles=frozenset({"Admin"}),
                    kind="action",
                    requires_confirmation=True,
                    description=tool.description or "",
                    service="TestAgentService",
                ),
                lambda call, recipient: "Подтвердить",
            ),
        )
    )
    service, users, _ = build_service(agent_settings, model=FunctionModel(model), registry=registry)
    users.find_all_user_roles.return_value = {"Admin"}
    pending = await service.answer(conversation, "Выполни действие")
    assert pending.confirmation_id is not None
    service.runtime.bind_message(pending.confirmation_id, 55)
    reply = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert reply.status == AgentReplyStatus.UNAVAILABLE
    assert executions == 1
