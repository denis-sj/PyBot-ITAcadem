"""Agent meeting actions require an author-owned confirmation and use exact previews."""

from unittest.mock import Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from pybot.dto.agent import AgentReplyStatus
from pybot.services.agent.tools import AgentToolRegistry
from pybot.services.ports.agent import AgentActionsPort
from tests.agent.conftest import build_service

_CREATE_ARGUMENTS = {
    "title": "Разбор Docker Compose",
    "meeting_date": "2026-10-18",
    "start_time": "14:00",
    "venue": "Коворкинг на Ленина, 5",
    "audience_kind": "all",
    "audience_target": None,
    "description": "Практический разбор.",
}


@pytest.mark.asyncio
async def test_create_meeting_shows_service_preview_before_author_confirmation(agent_settings, conversation) -> None:
    actions = Mock(spec=AgentActionsPort)
    actions.preview_meeting_creation.return_value = (
        "Создать встречу «Разбор Docker Compose»?\n"
        "Когда: в воскресенье, 18 октября, в 14:00\n"
        "Место: Коворкинг на Ленина, 5\n"
        "Аудитория: все участники\n"
        "Анонс в группе: Встречи"
    )
    actions.create_meeting.return_value = "Встреча создана, анонс опубликован."

    model = FunctionModel(
        lambda _messages, _info: ModelResponse(
            parts=[ToolCallPart("create_community_meeting", _CREATE_ARGUMENTS, tool_call_id="meeting-create")]
        )
    )
    service, users, memory = build_service(agent_settings, model=model, actions=actions)
    users.find_all_user_roles.return_value = {"Mentor"}

    pending = await service.answer(conversation, "Создай встречу в воскресенье в 14:00")

    assert pending.status == AgentReplyStatus.SUCCESS
    assert pending.confirmation_id is not None
    assert "Анонс в группе: Встречи" in pending.text
    assert "Приглашение получат:" not in pending.text
    assert actions.preview_meeting_creation.await_count == 2
    actions.create_meeting.assert_not_awaited()
    memory.append.assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 55)
    confirmed = await service.confirm(pending.confirmation_id, conversation, 55, approved=True)

    assert confirmed.text == "Встреча создана, анонс опубликован."
    actions.create_meeting.assert_awaited_once()
    actor_id, draft = actions.create_meeting.await_args.args
    assert actor_id == 42
    assert draft.title == "Разбор Docker Compose"
    assert draft.meeting_date.isoformat() == "2026-10-18"
    assert draft.start_time == "14:00"
    memory.append.assert_awaited_once_with(conversation, "Создай встречу в воскресенье в 14:00", confirmed.text)


def test_meeting_create_role_policy_and_dynamic_owner_actions() -> None:
    registry = AgentToolRegistry()
    student_tools = registry.for_roles(frozenset({"Student"})).policies
    mentor_tools = registry.for_roles(frozenset({"Mentor"})).policies

    assert "create_community_meeting" not in student_tools
    assert "create_community_meeting" in mentor_tools
    for name in ("reschedule_community_meeting", "cancel_community_meeting"):
        assert name not in student_tools
        assert name in mentor_tools
    for name in ("create_community_meeting", "reschedule_community_meeting", "cancel_community_meeting"):
        entry = next(item for item in registry.entries if item.tool.name == name)
        assert entry.tool.requires_approval is True
        assert entry.confirmation_previewer is not None
