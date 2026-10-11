from datetime import UTC, date, datetime, time
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from pybot.core.constants import YEKATERINBURG_TZ, PointsTypeEnum
from pybot.dto import ProfileViewDTO, UserReadDTO
from pybot.dto.agent_read import AgentMonth
from pybot.dto.attendance import (
    AttendanceMonthlyRankingItemDTO,
    AttendanceRecordReadDTO,
    AttendanceTopDTO,
    AttendanceUserSummaryDTO,
)
from pybot.dto.competence_dto import CompetenceReadDTO
from pybot.dto.leaderboard_dto import LeaderboardPeriod, WeeklyLeaderboardRowDTO
from pybot.dto.level_dto import LevelReadDTO
from pybot.dto.role_dto import RoleReadDTO
from pybot.dto.user_dto import UserLevelReadDTO
from pybot.dto.value_objects import Points
from pybot.services.agent.reads import AgentReadService
from pybot.services.attendance_service import AttendanceService
from pybot.services.competence import CompetenceService
from pybot.services.leaderboard import LeaderboardService
from pybot.services.user_services.user_competence import UserCompetenceService
from pybot.services.user_services.user_profile import UserProfileService
from pybot.services.user_services.user_roles import UserRolesService
from pybot.services.user_services.users import UserService


@pytest.fixture
def reads():
    return AgentReadService(
        Mock(spec=UserProfileService),
        Mock(spec=AttendanceService),
        Mock(spec=LeaderboardService),
        Mock(spec=UserRolesService),
        Mock(spec=UserCompetenceService),
        Mock(spec=CompetenceService),
        Mock(spec=UserService),
    )


def summary():
    return AttendanceUserSummaryDTO(
        user_id=98765,
        user_full_name="Студент",
        total_minutes=90,
        rank=2,
        total_participants=7,
        visits_count=1,
    )


@pytest.mark.asyncio
async def test_month_choice_does_not_query_visits_until_explicit_selection(reads):
    reads.attendance.get_user_visit_months.return_value = [(2020, 9), (2020, 8)]
    result = await reads.get_visits(98765)
    assert "Уточните месяц" in result and "2020-09" in result and "2020-08" in result
    reads.attendance.get_user_monthly_visits.assert_not_awaited()
    reads.attendance.get_user_monthly_summary.assert_not_awaited()
    reads.attendance.get_user_monthly_summary.return_value = summary()
    reads.attendance.get_user_monthly_visits.return_value = [
        AttendanceRecordReadDTO(
            id=78901,
            user_id=98765,
            import_id=55555,
            user_full_name="Студент",
            date=date(2020, 9, 9),
            start_time=time(18),
            end_time=time(19, 30),
            duration_minutes=90,
            source="manual",
            confirmed_at=datetime.now(UTC),
        )
    ]
    result = await reads.get_visits(98765, AgentMonth(year=2020, month=9))
    reads.attendance.get_user_monthly_visits.assert_awaited_once_with(98765, year=2020, month=9)
    assert "2020-09-09" in result and "18:00–19:30" in result and "90 минут (1 ч 30 мин)" in result
    assert all(value not in result for value in ("98765", "78901", "55555"))


@pytest.mark.asyncio
async def test_only_available_month_is_selected_automatically(reads):
    reads.attendance.get_user_visit_months.return_value = [(2020, 9)]
    reads.attendance.get_user_monthly_summary.return_value = summary()
    reads.attendance.get_user_monthly_visits.return_value = []
    assert "2020-09" in await reads.get_visits(98765)
    reads.attendance.get_user_monthly_visits.assert_awaited_once_with(98765, year=2020, month=9)


@pytest.mark.asyncio
@pytest.mark.parametrize("months", [[], [(2020, 8)]])
async def test_empty_or_unavailable_month_does_not_read_other_month(reads, months):
    reads.attendance.get_user_visit_months.return_value = months
    assert "нет" in await reads.get_visits(98765, AgentMonth(year=2020, month=9))
    reads.attendance.get_user_monthly_visits.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_defaults_to_current_business_month(reads):
    reads.attendance.get_user_monthly_summary.return_value = summary()
    period = AgentMonth.current()
    assert "место: 2" in await reads.get_attendance_summary(98765)
    reads.attendance.get_user_monthly_summary.assert_awaited_once_with(98765, year=period.year, month=period.month)


@pytest.mark.asyncio
async def test_attendance_top_preserves_service_ties_and_own_rank(reads):
    rows = [
        AttendanceMonthlyRankingItemDTO(
            rank=rank,
            user_id=98765 + index,
            user_full_name=f"Студент {index}",
            total_minutes=90,
            visits_count=1,
        )
        for index, rank in enumerate((1, 2, 2))
    ]
    own = rows[1].model_copy(update={"rank": 7})
    top = AttendanceTopDTO(
        period_start=date(2020, 9, 1),
        period_end=date(2020, 9, 30),
        rows=rows,
        total_participants=7,
        current_user=own,
    )
    reads.attendance.get_month_top.return_value = top
    result = await reads.get_month_top(98765, AgentMonth(year=2020, month=9))
    reads.attendance.get_month_top.assert_awaited_once_with(98765, year=2020, month=9)
    assert result.count("Место 2:") == 2 and "Моё место: Место 7:" in result
    assert "98765" not in result
    reads.attendance.get_week_top.return_value = top
    assert "Топ посещаемости недели" in await reads.get_week_top(98765)
    reads.attendance.get_week_top.assert_awaited_once_with(98765)


@pytest.mark.asyncio
async def test_empty_top_and_default_month(reads):
    reads.attendance.get_month_top.return_value = AttendanceTopDTO(
        period_start=date(2020, 9, 1),
        period_end=date(2020, 9, 30),
    )
    assert "Посещений за период нет" in await reads.get_month_top(98765)
    period = AgentMonth.current()
    reads.attendance.get_month_top.assert_awaited_once_with(98765, year=period.year, month=period.month)


@pytest.mark.asyncio
async def test_points_leaderboard_uses_both_point_types_and_exclusive_week_end(reads):
    start = datetime(2020, 9, 7, tzinfo=YEKATERINBURG_TZ)
    end = datetime(2020, 9, 14, tzinfo=YEKATERINBURG_TZ)
    reads.leaderboard.get_previous_calendar_week_period.return_value = LeaderboardPeriod(start=start, end=end)
    reads.leaderboard.get_previous_calendar_week_leaderboard.side_effect = [
        [
            WeeklyLeaderboardRowDTO(
                user_id=98765,
                telegram_id=77777,
                first_name="Студент",
                last_name=None,
                patronymic=None,
                total_points_delta=12,
                points_type=PointsTypeEnum.ACADEMIC,
                period_start=start,
                period_end=end,
            )
        ],
        [],
    ]
    result = await reads.get_points_leaderboard()
    assert "2020-09-07–2020-09-13" in result and "Студент: 12" in result
    assert "Репутационные баллы" in result and "Начислений за период нет" in result
    assert "77777" not in result and "98765" not in result
    assert [
        call.kwargs["points_type"] for call in reads.leaderboard.get_previous_calendar_week_leaderboard.await_args_list
    ] == list(PointsTypeEnum)
    reads.attendance.get_week_top.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalogs_and_self_assignments_do_not_expose_ids(reads):
    reads.roles.find_all_roles.return_value = [RoleReadDTO(id=98765, name="Student", description="Студент")]
    reads.competences.find_all_competencies.return_value = [
        CompetenceReadDTO(id=98765, name="Python", description=None)
    ]
    reads.roles.find_user_roles.return_value = ["Student"]
    reads.user_competences.find_user_competencies.return_value = []
    assert await reads.get_roles_catalog() == "Student: Студент"
    assert await reads.get_competences_catalog() == "Python: без описания"
    assert await reads.get_user_roles(98765) == "Мои роли: Student"
    assert await reads.get_user_competences(98765) == "Мои компетенции: не назначены"
    reads.roles.find_user_roles.assert_awaited_once_with(98765)
    reads.user_competences.find_user_competencies.assert_awaited_once_with(98765)
    reads.roles.find_all_roles.return_value = []
    reads.competences.find_all_competencies.return_value = []
    assert "пуст" in await reads.get_roles_catalog()
    assert "пуст" in await reads.get_competences_catalog()


@pytest.mark.asyncio
async def test_profile_reuses_calculated_progress_and_omits_private_identifiers(reads):
    academic = Points(value=75, point_type=PointsTypeEnum.ACADEMIC)
    reputation = Points(value=12, point_type=PointsTypeEnum.REPUTATION)
    user = UserReadDTO(
        id=98765,
        telegram_id=77777,
        first_name="Иван",
        last_name="Иванов",
        patronymic=None,
        academic_points=academic,
        reputation_points=reputation,
        join_date=date(2020, 9, 1),
    )
    academic_level = UserLevelReadDTO(
        current_level=LevelReadDTO(name="Начальный", required_points=academic),
        next_level=LevelReadDTO(name="Следующий", required_points=academic),
    )
    reputation_level = UserLevelReadDTO(
        current_level=LevelReadDTO(name="Новичок", required_points=reputation),
        next_level=LevelReadDTO(name="Следующий", required_points=reputation),
    )
    reads.profiles.build_profile_view.return_value = ProfileViewDTO(
        user=user,
        academic_progress=academic,
        academic_level=academic_level,
        academic_current_points=Points(value=25, point_type=PointsTypeEnum.ACADEMIC),
        academic_next_points=academic,
        reputation_progress=reputation,
        reputation_level=reputation_level,
        reputation_current_points=reputation,
        reputation_next_points=reputation,
        roles_data=["Student"],
        competences=[],
    )
    result = await reads.get_profile(user)
    reads.profiles.build_profile_view.assert_awaited_once_with(user)
    assert "Иванов Иван" in result and "баллы: 75" in result and "25 из 75" in result
    assert "баллы: 12" in result and "98765" not in result and "77777" not in result
    reads.users.find_user_by_telegram_id.return_value = user
    target_profile = await reads.get_profile_for_telegram_id(77777)
    assert target_profile.startswith("Профиль участника:")
    assert "Мой профиль:" not in target_profile


@pytest.mark.asyncio
async def test_admin_target_lookup_uses_telegram_id_and_existing_internal_id(reads, monkeypatch):
    user = UserReadDTO(
        id=42,
        telegram_id=771234,
        first_name="Анна",
        last_name="Иванова",
        patronymic=None,
        academic_points=Points(value=0, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2020, 1, 1),
    )
    reads.users.find_user_by_telegram_id.return_value = user
    formatted_profile = Mock(return_value="Профиль участника: Иванова Анна")
    monkeypatch.setattr(reads, "_format_profile", formatted_profile)
    target_profile = await reads.get_profile_for_telegram_id(771234)
    reads.users.find_user_by_telegram_id.assert_awaited_once_with(771234)
    reads.profiles.build_profile_view.assert_awaited_once_with(user)
    formatted_profile.assert_called_once_with(user, reads.profiles.build_profile_view.return_value, "Профиль участника")
    assert target_profile.startswith("Профиль участника: Иванова Анна")

    reads.attendance.get_user_monthly_summary.return_value = summary()
    text = await reads.get_attendance_for_telegram_id(771234, AgentMonth(year=2020, month=9))
    reads.attendance.get_user_monthly_summary.assert_awaited_once_with(42, year=2020, month=9)
    assert "Студент" in text and "98765" not in text and "771234" not in text


@pytest.mark.asyncio
async def test_admin_target_not_found_is_reported_without_leaking_identifier(reads):
    reads.users.find_user_by_telegram_id.return_value = None
    assert await reads.get_profile_for_telegram_id(771234) == "Пользователь не зарегистрирован."
    assert (
        await reads.get_attendance_for_telegram_id(771234, AgentMonth(year=2020, month=9))
        == "Пользователь не зарегистрирован."
    )
    reads.attendance.get_user_monthly_summary.assert_not_awaited()


@pytest.mark.parametrize(
    "period",
    [
        dict(year=2020, month=0),
        dict(year=2020, month=13),
        dict(year=0, month=1),
        dict(year=9999, month=1),
        dict(year=2020, month=9, user_id=42),
    ],
)
def test_invalid_period_or_recipient_is_rejected(period):
    with pytest.raises(ValidationError):
        AgentMonth.model_validate(period)
