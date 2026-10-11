"""Admin read tools preserve command-level roles and use explicit Telegram identities."""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from pybot.dto import UserReadDTO
from pybot.dto.attendance import (
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecordReadDTO,
)
from pybot.dto.agent_read import AgentMonth
from pybot.services.agent.reads import AgentReadService
from pybot.services.agent.tools import AgentToolRegistry
from tests.agent.conftest import build_service

ADMIN_READ_TOOLS = {
    "get_current_month",
    "get_club_attendance_report",
    "get_user_profile",
    "get_user_attendance",
    "get_current_chat_id",
    "find_user_candidates",
}
ADMIN_ACTION_TOOLS = {
    "adjust_user_points",
    "change_user_role",
    "change_user_competence",
    "update_attendance_visit_time",
    "delete_attendance_visit",
    "add_manual_attendance_visit",
}


@pytest.fixture
def reads():
    attendance = Mock()
    attendance.get_monthly_report = AsyncMock()
    attendance.get_sorted_participants = AsyncMock(return_value=[])
    return AgentReadService(Mock(), attendance, Mock(), Mock(), Mock(), Mock(), Mock())


def test_stage_two_admin_read_tools_are_hidden_from_students(agent_settings):
    registry = AgentToolRegistry()
    assert ADMIN_READ_TOOLS.isdisjoint(registry.for_roles(frozenset({"Student"})).policies)
    assert ADMIN_READ_TOOLS <= registry.for_roles(frozenset({"Admin"})).policies.keys()
    assert {"request_mentor_role"} | ADMIN_ACTION_TOOLS <= {
        entry.tool.name for entry in registry.entries if entry.tool.requires_approval
    }


def model_call(
    tool_name: str,
    arguments: dict[str, object],
    final_text: str = "Проверенные данные получены",
):
    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart(final_text)])
        return ModelResponse(parts=[ToolCallPart(tool_name, arguments, tool_call_id="admin-read")])

    return FunctionModel(model)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "method", "expected"),
    [
        ("get_current_month", {}, "get_current_accounting_month", ()),
        ("get_club_attendance_report", {"detailed": True}, "get_club_attendance_report", (True,)),
        ("find_user_candidates", {"name_query": "Иванов"}, "find_user_candidates", ("Иванов",)),
        ("get_user_profile", {"telegram_id": 771234}, "get_profile_for_telegram_id", (771234,)),
        (
            "get_user_attendance",
            {"telegram_id": 771234, "period": {"year": 2020, "month": 9}},
            "get_attendance_for_telegram_id",
            (771234, AgentMonth(year=2020, month=9)),
        ),
    ],
)
async def test_admin_reads_dispatch_to_existing_service(
    agent_settings, conversation, tool_name, arguments, method, expected
):
    reads = Mock(spec=AgentReadService)
    reads.get_current_accounting_month.return_value = "Расчётный месяц 2026-10."
    reads.get_club_attendance_report.return_value = "Отчёт за 2026-10."
    reads.get_profile_for_telegram_id.return_value = "Баллы: 75."
    reads.get_attendance_for_telegram_id.return_value = "Посещения: 2."
    reads.find_user_candidates.return_value = "Кандидаты: Иванов Иван — Telegram ID 771."
    service, users, _ = build_service(agent_settings, model=model_call(tool_name, arguments), reads=reads)
    users.find_all_user_roles.return_value = {"Admin"}
    result = await service.answer(conversation, "Выполни чтение")
    assert result.text == "Проверенные данные получены"
    if tool_name == "get_current_month":
        getattr(reads, method).assert_called_once_with(*expected)
    else:
        getattr(reads, method).assert_awaited_once_with(*expected)
    if tool_name == "get_user_profile":
        reads.get_profile_for_telegram_id.assert_awaited_once_with(771234)
    if tool_name == "get_user_attendance":
        reads.get_attendance_for_telegram_id.assert_awaited_once_with(771234, AgentMonth(year=2020, month=9))


@pytest.mark.asyncio
async def test_chat_id_uses_current_conversation_and_is_admin_only(agent_settings, conversation):
    expected_text = f"ID чата: {conversation.chat_id}. ID топика: {conversation.topic_id}."
    service, users, _ = build_service(agent_settings, model=model_call("get_current_chat_id", {}, expected_text))
    users.find_all_user_roles.return_value = {"Admin"}
    result = await service.answer(conversation, "ID этого чата")
    assert result.text == expected_text


@pytest.mark.asyncio
async def test_student_cannot_execute_admin_read_even_with_forged_call(agent_settings, conversation):
    reads = Mock(spec=AgentReadService)
    service, _, _ = build_service(
        agent_settings, model=model_call("get_club_attendance_report", {"detailed": True}), reads=reads
    )
    result = await service.answer(conversation, "Покажи отчёт")
    assert result.status.value == "unavailable"
    reads.get_club_attendance_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_role_is_rechecked_when_tool_executes(agent_settings, conversation):
    reads = Mock(spec=AgentReadService)
    service, users, _ = build_service(agent_settings, model=model_call("get_club_attendance_report", {}), reads=reads)
    users.find_all_user_roles.side_effect = [{"Admin"}, set()]
    result = await service.answer(conversation, "Покажи отчёт")
    assert result.status.value == "access_denied"
    reads.get_club_attendance_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_attendance_report_text_is_bounded_and_omits_internal_ids(reads):
    report = AttendanceMonthlyReportDTO(
        year=2020,
        month=9,
        total_club_minutes=90,
        active_participants_count=1,
        average_minutes_per_participant=90,
        rankings=[
            AttendanceMonthlyRankingItemDTO(
                rank=1, user_id=991234, user_full_name="Иван Иванов <user>", total_minutes=90, visits_count=1
            )
        ],
        records=[
            AttendanceRecordReadDTO(
                id=index,
                import_id=881234,
                user_id=991234,
                user_full_name="Иван Иванов",
                date=date(2020, 9, 9),
                start_time=time(18),
                end_time=time(19, 30),
                duration_minutes=90,
                source="manual",
                confirmed_at=datetime(2020, 9, 9, 12, tzinfo=UTC),
            )
            for index in range(1, 32)
        ],
    )
    reads.attendance.get_monthly_report.return_value = report
    text = await reads.get_club_attendance_report(detailed=True)
    reads.attendance.get_monthly_report.assert_awaited_once_with(detailed=True)
    assert "Сентябрь 2020" in text and "Всего: 90 минут (1 ч 30 мин)" in text
    assert "Иван Иванов <user>" in text
    assert "2020-09-09" in text and "18:00–19:30" in text
    assert "показаны первые 30 из 31" in text
    assert "991234" not in text and "881234" not in text
