from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.constants import RoleEnum
from pybot.db.models import UserAchievementAward
from pybot.domain.services.community_achievements import AchievementCode, calculate_attendance_achievements
from pybot.dto import AchievementPublicationDTO
from pybot.dto.attendance import AttendanceMonthlyRankingItemDTO
from pybot.infrastructure.community_achievement_repository import CommunityAchievementRepository
from pybot.presentation.texts.community_achievements import achievement_title
from pybot.services.community_achievements import CommunityAchievementService
from pybot.services.ports import CommunityAchievementDispatchPort, NotificationBlockedError


@pytest.mark.parametrize(
    ("code", "title"),
    [
        (AchievementCode.FIRST_STEP.value, "Первый шаг"),
        (AchievementCode.STREAK_4.value, "Постоянство · 4 недели"),
        (AchievementCode.STREAK_8.value, "Постоянство · 8 недель"),
        (AchievementCode.STREAK_16.value, "Постоянство · 16 недель"),
        (AchievementCode.VISITS_10.value, "Десятка"),
        (AchievementCode.VISITS_25.value, "25 посещений"),
        (AchievementCode.VISITS_50.value, "50 посещений"),
        (AchievementCode.COMEBACK.value, "Камбэк"),
        (AchievementCode.TOP_WEEK.value, "Топ недели"),
        (AchievementCode.GOAL_DONE.value, "Цель взята"),
        (AchievementCode.MENTOR.value, "Ментор"),
    ],
)
def test_every_achievement_code_has_a_russian_title(code: str, title: str) -> None:
    assert achievement_title(code) == title


class MemoryAchievementRepository(CommunityAchievementRepository):
    """Small in-memory persistence fake for service contract tests."""

    def __init__(self) -> None:
        self.awards: dict[tuple[int, str], UserAchievementAward] = {}
        self._next_id = 1
        self.inserted_silently: list[tuple[int, str]] = []
        self.notified_ids: list[int] = []
        self.published_ids: list[int] = []

    async def add_missing_awards(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        awards: Mapping[str, datetime],
        silent: bool = False,
    ) -> int:
        _ = db
        inserted_count = 0
        for code, achieved_at in awards.items():
            key = (user_id, code)
            if key in self.awards:
                continue
            self.awards[key] = UserAchievementAward(
                id=self._next_id,
                user_id=user_id,
                code=code,
                achieved_at=achieved_at,
                is_notified=silent,
                is_published=silent or code == AchievementCode.FIRST_STEP.value,
            )
            self._next_id += 1
            inserted_count += 1
            if silent:
                self.inserted_silently.append(key)
        return inserted_count

    async def find_unnotified_awards(self, db: AsyncSession, user_id: int) -> Sequence[UserAchievementAward]:
        _ = db
        return [
            award
            for (award_user_id, _), award in self.awards.items()
            if award_user_id == user_id and not award.is_notified
        ]

    async def mark_notified(self, db: AsyncSession, award_ids: Sequence[int]) -> None:
        _ = db
        self.notified_ids.extend(award_ids)
        for award in self.awards.values():
            if award.id in award_ids:
                award.is_notified = True

    async def find_unpublished_public_awards(self, db: AsyncSession) -> Sequence[AchievementPublicationDTO]:
        _ = db
        return [
            AchievementPublicationDTO(
                id=award.id,
                user_id=award.user_id,
                first_name="Тестовый участник",
                last_name=None,
                telegram_username=None,
                code=award.code,
                achieved_at=award.achieved_at,
            )
            for award in self.awards.values()
            if not award.is_published and award.code not in {"first_step", "top_week"}
        ]

    async def mark_published(self, db: AsyncSession, award_ids: Sequence[int]) -> None:
        _ = db
        self.published_ids.extend(award_ids)
        for award in self.awards.values():
            if award.id in award_ids:
                award.is_published = True

    async def mark_user_awards_published(
        self,
        db: AsyncSession,
        user_ids: Sequence[int],
        code: str,
    ) -> None:
        _ = db
        self.published_ids.extend(
            award.id
            for (user_id, award_code), award in self.awards.items()
            if user_id in user_ids and award_code == code
        )
        for (user_id, award_code), award in self.awards.items():
            if user_id in user_ids and award_code == code:
                award.is_published = True


def make_service(settings_obj, *, visit_dates=None, mentor=False, notifications=None, rankings=None):
    db = Mock()
    db.commit = AsyncMock()
    user = SimpleNamespace(id=17, telegram_id=771234, bot_is_blocked=False)

    user_repository = Mock()
    user_repository.find_user_by_id = AsyncMock(return_value=user)
    user_repository.find_all_users = AsyncMock(return_value=[user])
    user_repository.has_role = AsyncMock(return_value=mentor)

    attendance_repository = Mock()
    attendance_repository.find_user_visit_dates = AsyncMock(return_value=visit_dates or [])

    attendance_service = Mock()
    attendance_service.get_rankings_for_period = AsyncMock(return_value=rankings or [])

    achievement_repository = MemoryAchievementRepository()
    notification_service = notifications
    service = CommunityAchievementService(
        db,
        achievement_repository,
        attendance_repository,
        attendance_service,
        user_repository,
        notification_service,
        settings_obj,
    )
    return service, db, user, user_repository, attendance_repository, attendance_service, achievement_repository


@pytest.mark.parametrize(
    ("week_count", "code"),
    [
        (4, AchievementCode.STREAK_4),
        (8, AchievementCode.STREAK_8),
        (16, AchievementCode.STREAK_16),
    ],
)
def test_each_streak_badge_is_awarded_at_its_threshold(week_count, code) -> None:
    dates = [date(2025, 1, 6) + timedelta(days=7 * week) for week in range(week_count)]

    calculation = calculate_attendance_achievements(dates, today=date(2026, 1, 1))

    assert calculation.earned_on[code] == dates[-1]


@pytest.mark.asyncio
async def test_mentor_badge_is_awarded_for_an_existing_mentor(settings_obj) -> None:
    service, _, _, user_repository, _, _, achievement_repository = make_service(settings_obj, mentor=True)

    await service.recalculate_user(17)

    assert (17, AchievementCode.MENTOR.value) in achievement_repository.awards
    user_repository.has_role.assert_awaited_once_with(service._db, 17, RoleEnum.MENTOR.value)


@pytest.mark.asyncio
async def test_goal_badge_is_awarded_once_per_user(settings_obj) -> None:
    service, db, _, _, _, _, achievement_repository = make_service(settings_obj)

    first_count = await service.award_event_achievement(17, AchievementCode.GOAL_DONE)
    second_count = await service.award_event_achievement(17, AchievementCode.GOAL_DONE)

    assert first_count == 1
    assert second_count == 0
    assert len(achievement_repository.awards) == 1
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_top_week_uses_attendance_rankings_and_is_public(settings_obj) -> None:
    settings_obj.achievements_chat_id = -1001234567890
    settings_obj.achievements_topic_id = 32
    winner = AttendanceMonthlyRankingItemDTO(
        rank=1,
        user_id=17,
        user_full_name="Тестовый участник",
        total_minutes=90,
        visits_count=2,
    )
    notification_service = Mock()
    notification_service.send_message = AsyncMock()
    service, db, _, _, _, attendance_service, repository = make_service(
        settings_obj,
        notifications=notification_service,
        rankings=[winner],
    )

    count = await service.publish_top_week(today=date(2026, 10, 19))

    attendance_service.get_rankings_for_period.assert_awaited_once_with(date(2026, 10, 12), date(2026, 10, 17))
    assert count == 1
    assert (17, AchievementCode.TOP_WEEK.value) in repository.awards
    assert notification_service.send_message.await_count == 2
    group_call = notification_service.send_message.await_args
    assert group_call is not None
    group_message = group_call.args[0]
    assert group_message.recipient_id == -1001234567890
    assert group_message.message_thread_id == 32
    assert group_message.message == "🏆 Топ посещаемости за 12.10.2026–17.10.2026:\n1. Тестовый участник — 1 ч 30 мин"
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_silent_history_backfill_sends_no_messages_or_publications(settings_obj) -> None:
    old_visit = date(2020, 1, 6)
    rankings = [
        AttendanceMonthlyRankingItemDTO(
            rank=1,
            user_id=17,
            user_full_name="Тестовый участник",
            total_minutes=90,
            visits_count=1,
        )
    ]
    notification_service = Mock()
    notification_service.send_message = AsyncMock()
    service, _, _, _, _, attendance_service, repository = make_service(
        settings_obj,
        visit_dates=[old_visit],
        notifications=notification_service,
        rankings=rankings,
    )
    existing_pending_award = UserAchievementAward(
        id=99,
        user_id=17,
        code=AchievementCode.GOAL_DONE.value,
        achieved_at=datetime(2020, 1, 1, tzinfo=UTC),
        is_published=False,
        is_notified=False,
    )
    repository.awards[(17, AchievementCode.GOAL_DONE.value)] = existing_pending_award

    inserted_count = await service.backfill_history_silently()

    assert inserted_count == 2
    assert (17, AchievementCode.FIRST_STEP.value) in repository.inserted_silently
    assert (17, AchievementCode.TOP_WEEK.value) in repository.inserted_silently
    assert all(
        repository.awards[key].is_notified and repository.awards[key].is_published
        for key in repository.inserted_silently
    )
    assert existing_pending_award.is_notified is False
    assert existing_pending_award.is_published is False
    attendance_service.get_rankings_for_period.assert_awaited_once_with(old_visit, old_visit + timedelta(days=5))
    notification_service.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfilled_awards_are_not_published_by_digest_or_weekly_top(settings_obj) -> None:
    settings_obj.achievements_chat_id = -1001234567890
    settings_obj.achievements_topic_id = 32
    old_visit = date(2020, 1, 6)
    historical_winner = AttendanceMonthlyRankingItemDTO(
        rank=1,
        user_id=17,
        user_full_name="Тестовый участник",
        total_minutes=90,
        visits_count=1,
    )
    notifications = Mock()
    notifications.send_message = AsyncMock()
    service, _, _, _, _, attendance_service, repository = make_service(
        settings_obj,
        visit_dates=[old_visit],
        notifications=notifications,
    )
    attendance_service.get_rankings_for_period.side_effect = [[historical_winner], []]

    backfilled_count = await service.backfill_history_silently()
    digest_count = await service.publish_daily_digest()
    top_week_count = await service.publish_top_week(today=date(2026, 10, 19))

    assert backfilled_count == 2
    assert repository.awards[(17, AchievementCode.FIRST_STEP.value)].is_published is True
    assert repository.awards[(17, AchievementCode.FIRST_STEP.value)].is_notified is True
    assert repository.awards[(17, AchievementCode.TOP_WEEK.value)].is_published is True
    assert repository.awards[(17, AchievementCode.TOP_WEEK.value)].is_notified is True
    assert digest_count == 0
    assert top_week_count == 0
    notifications.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_recalculation_is_idempotent_and_does_not_revoke_awards(settings_obj) -> None:
    dates = [date(2025, 1, 1) + timedelta(days=day) for day in range(25)]
    service, _, _, _, attendance_repository, _, repository = make_service(settings_obj, visit_dates=dates)

    first_inserted = await service.recalculate_user(17)
    awarded_codes = {code for user_id, code in repository.awards if user_id == 17}
    attendance_repository.find_user_visit_dates.return_value = []
    second_inserted = await service.recalculate_user(17)

    assert first_inserted == 4
    assert second_inserted == 0
    assert {code for user_id, code in repository.awards if user_id == 17} == awarded_codes


@pytest.mark.asyncio
async def test_empty_daily_digest_does_not_send(settings_obj) -> None:
    settings_obj.achievements_chat_id = -1001234567890
    settings_obj.achievements_topic_id = 32
    notification_service = Mock()
    notification_service.send_message = AsyncMock()
    service, _, _, _, _, _, _ = make_service(settings_obj, notifications=notification_service)

    count = await service.publish_daily_digest()

    assert count == 0
    notification_service.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_bot_is_marked_without_failing_the_award(settings_obj) -> None:
    notification_service = Mock()
    notification_service.send_message = AsyncMock(side_effect=NotificationBlockedError(message="blocked"))
    service, db, user, _, _, _, repository = make_service(
        settings_obj,
        visit_dates=[date(2026, 10, 12)],
        notifications=notification_service,
    )

    await service.recalculate_user(17)

    assert user.bot_is_blocked is True
    assert repository.notified_ids
    assert db.commit.await_count == 2
    notification_service.send_message.assert_awaited_once()
