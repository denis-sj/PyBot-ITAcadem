"""Agent actions delegate to established application services and recheck state."""

from datetime import date, datetime, time
from unittest.mock import AsyncMock, Mock

import pytest

from pybot.core.constants import PointsTypeEnum, RoleEnum, TaskScheduleKind
from pybot.core.config import AppSettings
from pybot.domain.exceptions import (
    AttendanceDomainError,
    DomainError,
    InsufficientPointsBalanceError,
    RoleAlreadyAssignedError,
)
from pybot.dto import BroadcastResult, UserReadDTO
from pybot.dto.attendance import AttendanceRecordReadDTO
from pybot.dto.competence_dto import CompetenceReadDTO
from pybot.dto.role_dto import RoleReadDTO
from pybot.dto.value_objects import Points
from pybot.services.agent.actions import AgentActionService
from pybot.services.attendance_service import AttendanceService
from pybot.services.broadcast import BroadcastService
from pybot.services.competence import CompetenceService
from pybot.services.notification_facade import NotificationFacade
from pybot.services.points import PointsService
from pybot.services.role_request import RoleRequestService
from pybot.services.user_services.user_competence import UserCompetenceService
from pybot.services.user_services.user_roles import UserRolesService
from pybot.services.user_services.users import UserService


def user(telegram_id: int, user_id: int = 10, *, academic: int = 30, reputation: int = 15) -> UserReadDTO:
    return UserReadDTO(
        id=user_id,
        first_name="Иван",
        last_name="Иванов",
        patronymic=None,
        telegram_id=telegram_id,
        academic_points=Points(value=academic, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=reputation, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2026, 1, 1),
    )


@pytest.fixture
def actions(settings_obj: AppSettings):
    return AgentActionService(
        Mock(spec=RoleRequestService),
        Mock(spec=UserService),
        Mock(spec=PointsService),
        Mock(spec=UserRolesService),
        Mock(spec=UserCompetenceService),
        Mock(spec=CompetenceService),
        Mock(spec=AttendanceService),
        Mock(spec=NotificationFacade),
        Mock(spec=BroadcastService),
        settings_obj,
    )


@pytest.mark.asyncio
async def test_points_adjustment_validates_balance_and_notifies_recipient(actions):
    admin = user(11, user_id=1)
    recipient = user(22)
    updated = user(22, academic=40)
    actions.users.find_user_by_telegram_id = AsyncMock(return_value=recipient)
    actions.users.get_user = AsyncMock(return_value=admin)
    actions.points.change_points = AsyncMock(return_value=updated)
    actions.notifications.notify_user = AsyncMock()

    with pytest.raises(InsufficientPointsBalanceError):
        await actions.validate_points_adjustment(1, 22, PointsTypeEnum.ACADEMIC, -31)
    actions.points.change_points.assert_not_awaited()

    result = await actions.adjust_points(1, 22, PointsTypeEnum.ACADEMIC, 10, "за доклад")
    assert "Иванов Иван" in result and "начислено 10" in result
    actions.points.change_points.assert_awaited_once()
    notification_call = actions.notifications.notify_user.await_args
    assert notification_call is not None
    notification = notification_call.args[0]
    assert notification.recipient_id == 22
    assert notification.kind == TaskScheduleKind.IMMEDIATE
    assert "за доклад" in notification.message


@pytest.mark.asyncio
async def test_role_change_rejects_duplicate_and_uses_user_roles_service(actions):
    target = user(22)
    actions.users.find_user_by_telegram_id = AsyncMock(return_value=target)
    actions.roles.find_all_roles = AsyncMock(return_value=[RoleReadDTO(id=3, name="Mentor")])
    actions.roles.check_user_role = AsyncMock(return_value=True)

    with pytest.raises(RoleAlreadyAssignedError):
        await actions.validate_role_change(22, RoleEnum.MENTOR, add=True)
    actions.roles.add_user_role.assert_not_awaited()

    actions.roles.check_user_role.return_value = False
    actions.roles.add_user_role = AsyncMock(return_value=target)
    result = await actions.change_role(22, RoleEnum.MENTOR, add=True)
    assert "Роль Mentor выдана" in result
    actions.roles.add_user_role.assert_awaited_once_with(22, RoleEnum.MENTOR)


@pytest.mark.asyncio
async def test_competence_change_uses_catalog_and_existing_assignment_service(actions):
    target = user(22)
    competence = CompetenceReadDTO(id=4, name="Python")
    actions.users.find_user_by_telegram_id = AsyncMock(return_value=target)
    actions.competences.find_all_competencies = AsyncMock(return_value=[competence])
    actions.user_competences.find_user_competencies = AsyncMock(return_value=[])
    actions.user_competences.add_user_competencies_by_names = AsyncMock(return_value=target)

    result = await actions.change_competence(22, "Python", add=True)

    assert "Компетенция Python выдана" in result
    actions.user_competences.add_user_competencies_by_names.assert_awaited_once_with(10, ("Python",))


@pytest.mark.asyncio
async def test_attendance_edits_reuse_audit_service_and_current_admin(actions):
    admin = user(11, user_id=1)
    target = user(22)
    record = AttendanceRecordReadDTO(
        id=91,
        import_id=None,
        user_id=10,
        user_full_name="Иванов Иван",
        date=date(2026, 9, 1),
        start_time=time(18),
        end_time=time(19),
        duration_minutes=60,
        source="manual",
        confirmed_at=datetime(2026, 9, 1),
    )
    actions.users.find_user_by_telegram_id = AsyncMock(return_value=target)
    actions.attendance.get_user_monthly_visits = AsyncMock(return_value=[record])
    actions.attendance.edit_visit_time = AsyncMock()

    result = await actions.update_visit_time(22, date(2026, 9, 1), time(18), time(20), admin)

    assert "2026-09-01" in result and "18:00–20:00" in result
    actions.attendance.edit_visit_time.assert_awaited_once_with(
        record_id=91,
        new_start_time=time(18),
        new_end_time=time(20),
        admin_tg_id=11,
        admin_name="Иванов Иван",
    )


@pytest.mark.asyncio
async def test_manual_visit_preflight_rejects_future_and_duplicate_dates(actions, monkeypatch):
    from pybot.core.config import YEKATERINBURG_TZ

    monkeypatch.setattr(
        "pybot.services.agent.actions.datetime",
        Mock(now=Mock(return_value=datetime(2026, 9, 1, tzinfo=YEKATERINBURG_TZ))),
    )
    actions.users.find_user_by_telegram_id = AsyncMock(return_value=user(22))
    actions.attendance.has_user_visit_on_date = AsyncMock(return_value=False)

    await actions.validate_manual_visit(22, date(2026, 8, 31), time(18), time(19))
    actions.attendance.has_user_visit_on_date.return_value = True
    with pytest.raises(AttendanceDomainError, match="уже есть посещение"):
        await actions.validate_manual_visit(22, date(2026, 8, 31), time(18), time(19))
    assert actions.attendance.create_manual_visit.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audience", "target", "expected_method"),
    [("all", None, "broadcast_for_all"), ("role", "Mentor", "broadcast_for_users_with_role")],
)
async def test_broadcast_dispatches_all_and_role_audiences(actions, audience, target, expected_method):
    actions.broadcasts.broadcast_for_all = AsyncMock(return_value=BroadcastResult(attempted=4, sent=3))
    actions.broadcasts.broadcast_for_users_with_role = AsyncMock(return_value=BroadcastResult(attempted=4, sent=3))

    result = await actions.broadcast(audience, target, "Собрание клуба")

    assert "попыток 4, доставлено 3" in result
    getattr(actions.broadcasts, expected_method).assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_resolves_competence_and_preserves_exact_approved_text(actions):
    competence = CompetenceReadDTO(id=7, name="Python", description="Программирование")
    actions.competences.find_all_competencies = AsyncMock(return_value=[competence])
    actions.broadcasts.broadcast_for_users_with_competence = AsyncMock(
        return_value=BroadcastResult(attempted=2, sent=2)
    )

    await actions.validate_broadcast("competence", "python", "Собрание клуба")
    result = await actions.broadcast("competence", "python", "Собрание клуба")

    assert "доставлено 2" in result
    actions.broadcasts.broadcast_for_users_with_competence.assert_awaited_once()
    broadcast_call = actions.broadcasts.broadcast_for_users_with_competence.await_args
    assert broadcast_call is not None
    assert broadcast_call.args[0].competence_id == 7
    assert broadcast_call.args[0].message == "Собрание клуба"


@pytest.mark.asyncio
async def test_broadcast_rejects_empty_or_unknown_target_before_sending(actions):
    with pytest.raises(DomainError):
        await actions.validate_broadcast("all", None, "  \n ")
    with pytest.raises(DomainError):
        await actions.validate_broadcast("role", "Unknown", "Собрание клуба")

    actions.broadcasts.broadcast_for_all.assert_not_called()
    actions.broadcasts.broadcast_for_users_with_role.assert_not_called()
