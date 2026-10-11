"""Admin action tools require a code-owned confirmation and a fresh role check."""

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
import pytest

from pybot.core.constants import PointsTypeEnum
from pybot.services.agent.admin_action_tools import confirmation_summary
from pybot.services.agent.tools import AgentToolRegistry
from tests.agent.conftest import build_service

ADMIN_ACTION_TOOLS = {
    "adjust_user_points",
    "change_user_role",
    "change_user_competence",
    "update_attendance_visit_time",
    "delete_attendance_visit",
    "add_manual_attendance_visit",
}


def test_admin_mutation_tools_are_filtered_and_always_deferred():
    registry = AgentToolRegistry()
    student_tools = registry.for_roles(frozenset({"Student"})).policies
    admin_tools = registry.for_roles(frozenset({"Admin"})).policies

    assert ADMIN_ACTION_TOOLS.isdisjoint(student_tools)
    assert ADMIN_ACTION_TOOLS <= admin_tools.keys()
    assert ADMIN_ACTION_TOOLS <= {entry.tool.name for entry in registry.entries if entry.tool.requires_approval}


def test_points_confirmation_uses_exact_action_arguments():
    call = ToolCallPart(
        "adjust_user_points",
        {
            "telegram_id": 771234,
            "points_type": PointsTypeEnum.ACADEMIC.value,
            "delta": -12,
            "reason": "возврат за отменённый доклад",
        },
        tool_call_id="points",
    )

    assert confirmation_summary(call) == (
        "списать участнику Telegram ID 771234 12 академических баллов. Причина: «возврат за отменённый доклад»."
    )


async def admin_points_model(messages, info):
    if any(
        isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    ):
        return ModelResponse(parts=[TextPart("Баллы изменены")])
    return ModelResponse(
        parts=[
            ToolCallPart(
                "adjust_user_points",
                {
                    "telegram_id": 771234,
                    "points_type": PointsTypeEnum.ACADEMIC.value,
                    "delta": 10,
                    "reason": "за доклад",
                },
                tool_call_id="change-points",
            )
        ]
    )


@pytest.mark.asyncio
async def test_points_change_runs_only_after_confirmation(agent_settings, conversation):
    from pybot.services.ports.agent import AgentActionsPort
    from unittest.mock import Mock

    actions = Mock(spec=AgentActionsPort)
    actions.adjust_points.return_value = "Участнику Ивану начислено 10 баллов."
    service, users, memory = build_service(
        agent_settings,
        model=FunctionModel(admin_points_model),
        actions=actions,
    )
    users.find_all_user_roles.return_value = {"Admin"}

    pending = await service.answer(conversation, "Начисли 10 академических баллов за доклад")

    assert pending.confirmation_id is not None
    assert pending.text == ("начислить участнику Telegram ID 771234 10 академических баллов. Причина: «за доклад».")
    actions.validate_points_adjustment.assert_awaited_once_with(42, 771234, PointsTypeEnum.ACADEMIC, 10)
    actions.adjust_points.assert_not_awaited()
    memory.append.assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 99)
    result = await service.confirm(pending.confirmation_id, conversation, 99, approved=True)

    assert result.text == "Баллы изменены"
    actions.adjust_points.assert_awaited_once_with(42, 771234, PointsTypeEnum.ACADEMIC, 10, "за доклад")
    assert actions.validate_points_adjustment.await_count == 2
    memory.append.assert_awaited_once_with(conversation, "Начисли 10 академических баллов за доклад", "Баллы изменены")
