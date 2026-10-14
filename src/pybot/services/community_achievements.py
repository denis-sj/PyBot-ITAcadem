"""Application service for awarding, displaying, and publishing community achievements."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.config import YEKATERINBURG_TZ, AppSettings
from ..core.constants import RoleEnum
from ..db.models import User
from ..domain.services.community_achievements import AchievementCode, calculate_attendance_achievements
from ..dto import (
    AchievementAwardReadDTO,
    CommunityAchievementSummaryDTO,
    NotifyDTO,
)
from ..infrastructure import (
    AttendanceRepository,
    CommunityAchievementRepository,
    UserRepository,
)
from ..presentation.texts.community_achievements import (
    render_achievement_congratulation,
    render_achievement_digest,
    render_top_week_message,
)
from .attendance_service import AttendanceService
from .ports import NotificationBlockedError, NotificationPermanentError, NotificationPort, NotificationTemporaryError

_WEEKLY_TOP_PLACES = 3


class CommunityAchievementService:
    """Calculate immutable awards and coordinate Telegram delivery."""

    def __init__(  # noqa: PLR0913
        self,
        db: AsyncSession,
        community_achievement_repository: CommunityAchievementRepository,
        attendance_repository: AttendanceRepository,
        attendance_service: AttendanceService,
        user_repository: UserRepository,
        notification_service: NotificationPort | None,
        settings: AppSettings,
    ) -> None:
        self._db = db
        self._achievement_repository = community_achievement_repository
        self._attendance_repository = attendance_repository
        self._attendance_service = attendance_service
        self._user_repository = user_repository
        self._notification_service = notification_service
        self._settings = settings

    async def recalculate_user(self, user_id: int) -> int:
        """Award all current and historical attendance achievements for one user."""
        user = await self._user_repository.find_user_by_id(self._db, user_id)
        if user is None:
            return 0

        visit_dates = await self._attendance_repository.find_user_visit_dates(self._db, user_id)
        calculation = calculate_attendance_achievements(
            visit_dates,
            today=datetime.now(YEKATERINBURG_TZ).date(),
        )
        award_dates = {
            code.value: self._at_yekaterinburg_midnight(earned_on) for code, earned_on in calculation.earned_on.items()
        }
        if await self._user_repository.has_role(self._db, user_id, RoleEnum.MENTOR.value):
            award_dates[AchievementCode.MENTOR.value] = datetime.now(YEKATERINBURG_TZ)

        inserted_count = await self._achievement_repository.add_missing_awards(
            self._db,
            user_id=user_id,
            awards=award_dates,
        )
        await self._db.commit()
        await self._deliver_pending_congratulations(user_id, user)
        return inserted_count

    async def award_event_achievement(
        self,
        user_id: int,
        code: AchievementCode,
        *,
        achieved_at: datetime | None = None,
        silent: bool = False,
    ) -> int:
        """Award an event-based badge such as a completed goal, at most once per user."""
        user = await self._user_repository.find_user_by_id(self._db, user_id)
        if user is None:
            return 0

        award_time = achieved_at or datetime.now(YEKATERINBURG_TZ)
        inserted_count = await self._achievement_repository.add_missing_awards(
            self._db,
            user_id=user_id,
            awards={code.value: award_time},
            silent=silent,
        )
        await self._db.commit()
        if not silent:
            await self._deliver_pending_congratulations(user_id, user)
        return inserted_count

    async def get_user_summary(self, user_id: int) -> CommunityAchievementSummaryDTO:
        """Return the current streak and durable achievement history for profile readers."""
        visit_dates = await self._attendance_repository.find_user_visit_dates(self._db, user_id)
        calculation = calculate_attendance_achievements(
            visit_dates,
            today=datetime.now(YEKATERINBURG_TZ).date(),
        )
        achievements: Sequence[AchievementAwardReadDTO] = await self._achievement_repository.find_user_awards(
            self._db,
            user_id,
        )
        return CommunityAchievementSummaryDTO(
            current_streak=calculation.current_streak,
            achievements=achievements,
        )

    async def publish_daily_digest(self) -> int:
        """Publish and mark all pending notable awards as one daily group digest."""
        if not self._settings.achievements_publication_enabled or self._notification_service is None:
            return 0

        awards = await self._achievement_repository.find_unpublished_public_awards(self._db)
        if not awards:
            return 0

        chat_id = self._settings.achievements_chat_id
        topic_id = self._settings.achievements_topic_id
        if chat_id is None or topic_id is None:
            return 0

        await self._notification_service.send_message(
            NotifyDTO(
                recipient_id=chat_id,
                message_thread_id=topic_id,
                message=render_achievement_digest(awards),
                parse_mode="HTML",
            )
        )
        await self._achievement_repository.mark_published(self._db, [award.id for award in awards])
        await self._db.commit()
        return len(awards)

    async def publish_top_week(self, *, today: date | None = None) -> int:
        """Award the previous `/visits` weekly top and publish its winners in one post."""
        if not self._settings.achievements_publication_enabled or self._notification_service is None:
            return 0

        current_date = today or datetime.now(YEKATERINBURG_TZ).date()
        current_monday = current_date - timedelta(days=current_date.weekday())
        previous_monday = current_monday - timedelta(days=7)
        week_start, week_end = AttendanceService.get_attendance_week_bounds(previous_monday)
        rankings = await self._attendance_service.get_rankings_for_period(week_start, week_end)
        winners = [row for row in rankings if row.rank <= _WEEKLY_TOP_PLACES]
        if not winners:
            return 0

        achieved_at = datetime.now(YEKATERINBURG_TZ)
        inserted_user_ids: set[int] = set()
        for winner in winners:
            inserted = await self._achievement_repository.add_missing_awards(
                self._db,
                user_id=winner.user_id,
                awards={AchievementCode.TOP_WEEK.value: achieved_at},
            )
            if inserted:
                inserted_user_ids.add(winner.user_id)
        await self._db.commit()

        for user_id in inserted_user_ids:
            user = await self._user_repository.find_user_by_id(self._db, user_id)
            if user is not None:
                await self._deliver_pending_congratulations(user_id, user)

        chat_id = self._settings.achievements_chat_id
        topic_id = self._settings.achievements_topic_id
        if chat_id is None or topic_id is None:
            return 0

        await self._notification_service.send_message(
            NotifyDTO(
                recipient_id=chat_id,
                message_thread_id=topic_id,
                message=render_top_week_message(
                    winners,
                    start_date=week_start.strftime("%d.%m.%Y"),
                    end_date=week_end.strftime("%d.%m.%Y"),
                ),
                parse_mode="HTML",
            )
        )
        await self._achievement_repository.mark_user_awards_published(
            self._db,
            [winner.user_id for winner in winners],
            AchievementCode.TOP_WEEK.value,
        )
        await self._db.commit()
        return len(winners)

    async def backfill_history_silently(self) -> int:
        """Award all discoverable historical achievements without sending or publishing messages."""
        users = await self._user_repository.find_all_users(self._db)
        inserted_count = 0
        all_visit_dates: set[date] = set()
        for user in users:
            visit_dates = await self._attendance_repository.find_user_visit_dates(self._db, user.id)
            all_visit_dates.update(visit_dates)
            calculation = calculate_attendance_achievements(
                visit_dates,
                today=datetime.now(YEKATERINBURG_TZ).date(),
            )
            award_dates = {
                code.value: self._at_yekaterinburg_midnight(earned_on)
                for code, earned_on in calculation.earned_on.items()
            }
            if await self._user_repository.has_role(self._db, user.id, RoleEnum.MENTOR.value):
                award_dates[AchievementCode.MENTOR.value] = datetime.now(YEKATERINBURG_TZ)
            inserted_count += await self._achievement_repository.add_missing_awards(
                self._db,
                user_id=user.id,
                awards=award_dates,
                silent=True,
            )

        current_monday = datetime.now(YEKATERINBURG_TZ).date()
        current_monday -= timedelta(days=current_monday.weekday())
        visit_weeks = sorted(
            {
                visit_date - timedelta(days=visit_date.weekday())
                for visit_date in all_visit_dates
                if visit_date - timedelta(days=visit_date.weekday()) < current_monday
            }
        )
        for week_start in visit_weeks:
            week_end = week_start + timedelta(days=5)
            rankings = await self._attendance_service.get_rankings_for_period(week_start, week_end)
            achieved_at = self._at_yekaterinburg_midnight(week_start + timedelta(days=7))
            for ranking in rankings:
                if ranking.rank > _WEEKLY_TOP_PLACES:
                    continue
                inserted_count += await self._achievement_repository.add_missing_awards(
                    self._db,
                    user_id=ranking.user_id,
                    awards={AchievementCode.TOP_WEEK.value: achieved_at},
                    silent=True,
                )

        await self._db.commit()
        return inserted_count

    async def _deliver_pending_congratulations(self, user_id: int, user: User) -> None:
        """Deliver outstanding personal badges, recording a Telegram 403 as blocked."""
        if self._notification_service is None:
            return

        awards = await self._achievement_repository.find_unnotified_awards(self._db, user_id)
        if not awards:
            return

        award_ids = [award.id for award in awards]
        if user.bot_is_blocked:
            await self._achievement_repository.mark_notified(self._db, award_ids)
            await self._db.commit()
            return

        try:
            await self._notification_service.send_message(
                NotifyDTO(
                    recipient_id=user.telegram_id,
                    message=render_achievement_congratulation([award.code for award in awards]),
                )
            )
        except NotificationBlockedError:
            user.bot_is_blocked = True
            await self._achievement_repository.mark_notified(self._db, award_ids)
            await self._db.commit()
            logger.info("событие=achievement_notification status=blocked user_id={user_id}", user_id=user_id)
            return
        except NotificationTemporaryError:
            raise
        except NotificationPermanentError as error:
            logger.warning(
                "Failed to deliver community achievement congratulations | user_id={user_id} error={error}",
                user_id=user_id,
                error=str(error),
            )
            await self._achievement_repository.mark_notified(self._db, award_ids)
            await self._db.commit()
            return

        await self._achievement_repository.mark_notified(self._db, award_ids)
        await self._db.commit()

    @staticmethod
    def _at_yekaterinburg_midnight(value: date) -> datetime:
        """Attach the community's business timezone to a historical calendar date."""
        return datetime.combine(value, time.min, tzinfo=YEKATERINBURG_TZ)
