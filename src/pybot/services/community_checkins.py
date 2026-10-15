"""Orchestrate weekly check-in prompts, answers, reminders, and streak warnings."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.config import YEKATERINBURG_TZ
from ..db.models import User
from ..domain.services.community_achievements import calculate_attendance_achievements
from ..domain.services.community_checkins import CheckinStatus, get_current_checkin_week_start, is_checkin_response_open
from ..domain.services.technology_stack import TechnologyStackValidationError, normalize_technology_name
from ..dto import NotifyDTO
from ..dto.community_checkins import ParsedCheckinResponseDTO
from ..infrastructure.attendance_repository import AttendanceRepository
from ..infrastructure.user_notification_preference_repository import UserNotificationPreferenceRepository
from ..infrastructure.weekly_checkin_repository import WeeklyCheckinRepository
from ..presentation.texts.community_checkins import (
    CHECKIN_PARSE_RETRY,
    CHECKIN_SAVED,
    STREAK_WARNING,
    render_checkin_prompt,
    render_checkin_reminder,
)
from ..presentation.texts.technology_stack import render_helper_recommendation
from .community_goals import CommunityGoalService
from .ports import NotificationBlockedError, NotificationPermanentError, NotificationPort, NotificationTemporaryError
from .ports.checkin_response_parser import CheckinResponseParserPort
from .technology_stack import TechnologyStackService

_MIN_STREAK_FOR_WARNING = 3


class CommunityCheckinService:
    """Keep source messages transient and persist only bounded, model-extracted fields."""

    def __init__(  # noqa: PLR0913
        self,
        db: AsyncSession,
        repository: WeeklyCheckinRepository,
        attendance_repository: AttendanceRepository,
        preferences: UserNotificationPreferenceRepository,
        goals: CommunityGoalService,
        stack: TechnologyStackService,
        parser: CheckinResponseParserPort,
        notifications: NotificationPort,
    ) -> None:
        self._db = db
        self._repository = repository
        self._attendance_repository = attendance_repository
        self._preferences = preferences
        self._goals = goals
        self._stack = stack
        self._parser = parser
        self._notifications = notifications

    async def deliver_weekly_prompts(self, *, now: datetime | None = None) -> int:
        """Send one Wednesday prompt to each currently eligible Student."""
        local_now = self._local_now(now)
        week_start = get_current_checkin_week_start(local_now)
        recipients = await self._repository.find_prompt_recipients(self._db)
        await self._repository.create_missing_campaign_rows(self._db, week_start=week_start, recipients=recipients)
        await self._db.commit()

        sent_count = 0
        for recipient in recipients:
            if not await self._repository.claim_prompt(
                self._db,
                user_id=recipient.user_id,
                week_start=week_start,
                now=local_now,
            ):
                continue
            await self._db.commit()
            try:
                await self._notifications.send_message(
                    NotifyDTO(
                        recipient_id=recipient.telegram_id,
                        message=render_checkin_prompt(recipient.goal_text, recipient.has_technology_stack),
                    )
                )
            except NotificationBlockedError:
                await self._mark_user_blocked(recipient.user_id)
                continue
            except NotificationTemporaryError:
                await self._repository.release_prompt_claim(
                    self._db,
                    user_id=recipient.user_id,
                    week_start=week_start,
                )
                await self._db.commit()
                raise
            except NotificationPermanentError as error:
                logger.warning(
                    "event=community_checkin_prompt status=failed user_id={user_id} error={error}",
                    user_id=recipient.user_id,
                    error=str(error),
                )
                continue
            sent_count += 1
        return sent_count

    async def handle_response(self, user_id: int, source_text: str, *, now: datetime | None = None) -> str | None:
        """Consume the next private non-command message while a check-in is open."""
        local_now = self._local_now(now)
        current_week = get_current_checkin_week_start(local_now)
        campaign = await self._repository.find_latest_prompted_checkin(
            self._db,
            user_id=user_id,
            earliest_week_start=current_week - timedelta(days=7),
            latest_week_start=current_week,
        )
        if campaign is None or not is_checkin_response_open(campaign.week_start, local_now):
            return None

        try:
            parsed = await self._parser.parse(source_text)
            parsed = self._normalize_parsed_response(parsed)
        except Exception:
            logger.exception("event=community_checkin_parse status=failed user_id={user_id}", user_id=user_id)
            return CHECKIN_PARSE_RETRY

        await self._repository.save_response(
            self._db,
            checkin_id=campaign.id,
            response=parsed,
            answered_at=local_now,
        )
        await self._db.commit()

        if parsed.status == CheckinStatus.GOAL_ACHIEVED:
            await self._goals.complete_active_goal(user_id)
        if parsed.new_goal is not None:
            await self._goals.set_goal(user_id, parsed.new_goal)

        message_parts = [CHECKIN_SAVED]
        if parsed.status == CheckinStatus.STUCK and parsed.help_topic is not None:
            recommendation = await self._stack.find_helpers(user_id, parsed.help_topic)
            message_parts.append(render_helper_recommendation(recommendation))
        return "\n\n".join(message_parts)

    async def send_weekly_reminders(self, *, now: datetime | None = None) -> int:
        """Remind Wednesday campaign participants who still have no answer."""
        local_now = self._local_now(now)
        week_start = get_current_checkin_week_start(local_now)
        recipients = await self._repository.find_reminder_recipients(self._db, week_start=week_start)
        sent_count = 0
        for recipient in recipients:
            if not await self._repository.mark_reminder_sent(self._db, checkin_id=recipient.checkin_id, now=local_now):
                continue
            await self._db.commit()
            try:
                await self._notifications.send_message(
                    NotifyDTO(
                        recipient_id=recipient.telegram_id,
                        message=render_checkin_reminder(recipient.goal_text),
                    )
                )
            except NotificationBlockedError:
                await self._mark_user_blocked(recipient.user_id)
                continue
            except NotificationTemporaryError:
                await self._repository.release_reminder_claim(self._db, checkin_id=recipient.checkin_id)
                await self._db.commit()
                raise
            except NotificationPermanentError as error:
                logger.warning(
                    "event=community_checkin_reminder status=failed user_id={user_id} error={error}",
                    user_id=recipient.user_id,
                    error=str(error),
                )
                continue
            sent_count += 1
        return sent_count

    async def warn_at_risk_streaks(self, *, now: datetime | None = None) -> int:
        """Send one Saturday warning to subscribed participants with an unbroken streak of three weeks."""
        local_now = self._local_now(now)
        week_start = get_current_checkin_week_start(local_now)
        recipients = await self._repository.find_streak_warning_recipients(self._db)
        visit_dates_by_user = await self._attendance_repository.find_visit_dates_for_users(
            self._db,
            [recipient.user_id for recipient in recipients],
        )
        sent_count = 0
        for recipient in recipients:
            visit_dates = visit_dates_by_user.get(recipient.user_id, [])
            calculation = calculate_attendance_achievements(visit_dates, today=local_now.date())
            if calculation.current_streak < _MIN_STREAK_FOR_WARNING or any(
                visit_date - timedelta(days=visit_date.weekday()) == week_start for visit_date in visit_dates
            ):
                continue
            claimed = await self._preferences.claim_streak_warning(
                self._db,
                user_id=recipient.user_id,
                week_start=week_start,
            )
            if not claimed:
                continue
            await self._db.commit()
            try:
                await self._notifications.send_message(
                    NotifyDTO(
                        recipient_id=recipient.telegram_id,
                        message=STREAK_WARNING.format(streak=calculation.current_streak),
                    )
                )
            except NotificationBlockedError:
                await self._mark_user_blocked(recipient.user_id)
                continue
            except NotificationTemporaryError:
                await self._reset_streak_warning_claim(recipient.user_id, week_start)
                raise
            except NotificationPermanentError as error:
                logger.warning(
                    "event=streak_warning status=failed user_id={user_id} error={error}",
                    user_id=recipient.user_id,
                    error=str(error),
                )
                continue
            sent_count += 1
        return sent_count

    @staticmethod
    def _local_now(now: datetime | None) -> datetime:
        return (now or datetime.now(YEKATERINBURG_TZ)).astimezone(YEKATERINBURG_TZ)

    @staticmethod
    def _normalize_parsed_response(parsed: ParsedCheckinResponseDTO) -> ParsedCheckinResponseDTO:
        if parsed.help_topic is None:
            return parsed
        try:
            normalized_topic = normalize_technology_name(parsed.help_topic)
        except TechnologyStackValidationError:
            return parsed.model_copy(update={"help_topic": None})
        return parsed.model_copy(update={"help_topic": normalized_topic})

    async def _mark_user_blocked(self, user_id: int) -> None:
        user = await self._db.scalar(select(User).where(User.id == user_id))
        if user is not None:
            user.bot_is_blocked = True
            await self._db.commit()

    async def _reset_streak_warning_claim(self, user_id: int, week_start: date) -> None:
        await self._preferences.release_streak_warning_claim(self._db, user_id=user_id, week_start=week_start)
        await self._db.commit()
