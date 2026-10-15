"""Persistence queries for weekly check-in campaigns and pulse report participants."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.constants import RoleEnum
from ..db.models import (
    Role,
    User,
    UserGoal,
    UserNotificationPreference,
    UserRole,
    UserTechnologyStack,
    WeeklyCheckin,
)
from ..domain.services.community_checkins import GoalStatus
from ..dto.community_checkins import (
    CheckinPromptRecipientDTO,
    CheckinReminderRecipientDTO,
    ParsedCheckinResponseDTO,
    PulseReportRecipientDTO,
    StreakWarningRecipientDTO,
    WeeklyCheckinReportRowDTO,
)


class WeeklyCheckinRepository:
    """Store response summaries and retrieve the data required by scheduled jobs."""

    async def find_prompt_recipients(self, db: AsyncSession) -> Sequence[CheckinPromptRecipientDTO]:
        """Select unblocked Students who have not disabled check-ins."""
        statement = (
            select(
                User.id.label("user_id"),
                User.telegram_id,
                User.first_name,
                UserGoal.id.label("goal_id"),
                UserGoal.text.label("goal_text"),
            )
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .outerjoin(
                UserGoal,
                and_(UserGoal.user_id == User.id, UserGoal.status == GoalStatus.ACTIVE.value),
            )
            .outerjoin(UserNotificationPreference, UserNotificationPreference.user_id == User.id)
            .where(
                Role.name == RoleEnum.STUDENT.value,
                User.bot_is_blocked.is_(False),
                or_(
                    UserNotificationPreference.id.is_(None),
                    UserNotificationPreference.checkins_enabled.is_(True),
                ),
            )
            .order_by(User.id)
        )
        result = await db.execute(statement)
        rows = result.all()
        if not rows:
            return ()

        user_ids = [row.user_id for row in rows]
        stack_user_ids = set(
            (
                await db.scalars(
                    select(UserTechnologyStack.user_id).where(UserTechnologyStack.user_id.in_(user_ids)).distinct()
                )
            ).all()
        )
        return tuple(
            CheckinPromptRecipientDTO(
                user_id=row.user_id,
                telegram_id=row.telegram_id,
                first_name=row.first_name,
                goal_id=row.goal_id,
                goal_text=row.goal_text,
                has_technology_stack=row.user_id in stack_user_ids,
            )
            for row in rows
        )

    async def find_streak_warning_recipients(self, db: AsyncSession) -> Sequence[StreakWarningRecipientDTO]:
        """Select available Students who have not opted out of streak warnings."""
        statement = (
            select(User.id, User.telegram_id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .outerjoin(UserNotificationPreference, UserNotificationPreference.user_id == User.id)
            .where(
                Role.name == RoleEnum.STUDENT.value,
                User.bot_is_blocked.is_(False),
                or_(
                    UserNotificationPreference.id.is_(None),
                    UserNotificationPreference.streak_warnings_enabled.is_(True),
                ),
            )
            .order_by(User.id)
        )
        result = await db.execute(statement)
        return tuple(
            StreakWarningRecipientDTO(user_id=user_id, telegram_id=telegram_id) for user_id, telegram_id in result.all()
        )

    async def create_missing_campaign_rows(
        self,
        db: AsyncSession,
        *,
        week_start: date,
        recipients: Sequence[CheckinPromptRecipientDTO],
    ) -> None:
        """Prepare one idempotent campaign row for each selected participant."""
        if not recipients:
            return
        statement = (
            insert(WeeklyCheckin)
            .values(
                [
                    {"user_id": recipient.user_id, "week_start": week_start, "goal_id": recipient.goal_id}
                    for recipient in recipients
                ]
            )
            .on_conflict_do_nothing(index_elements=[WeeklyCheckin.user_id, WeeklyCheckin.week_start])
        )
        await db.execute(statement)

    async def claim_prompt(self, db: AsyncSession, *, user_id: int, week_start: date, now: datetime) -> bool:
        """Claim an unsent prompt so overlapping scheduler jobs do not duplicate it."""
        statement = (
            update(WeeklyCheckin)
            .where(
                WeeklyCheckin.user_id == user_id,
                WeeklyCheckin.week_start == week_start,
                WeeklyCheckin.prompted_at.is_(None),
            )
            .values(prompted_at=now)
            .returning(WeeklyCheckin.id)
        )
        return await db.scalar(statement) is not None

    async def release_prompt_claim(self, db: AsyncSession, *, user_id: int, week_start: date) -> None:
        """Allow a later retry when prompt delivery failed temporarily."""
        await db.execute(
            update(WeeklyCheckin)
            .where(WeeklyCheckin.user_id == user_id, WeeklyCheckin.week_start == week_start)
            .values(prompted_at=None)
        )

    async def find_latest_prompted_checkin(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        earliest_week_start: date,
        latest_week_start: date,
    ) -> WeeklyCheckin | None:
        """Find the latest prompted opportunity within the current response window."""
        return await db.scalar(
            select(WeeklyCheckin)
            .where(
                WeeklyCheckin.user_id == user_id,
                WeeklyCheckin.week_start >= earliest_week_start,
                WeeklyCheckin.week_start <= latest_week_start,
                WeeklyCheckin.prompted_at.is_not(None),
            )
            .order_by(WeeklyCheckin.week_start.desc())
            .limit(1)
        )

    async def find_reminder_recipients(
        self,
        db: AsyncSession,
        *,
        week_start: date,
    ) -> Sequence[CheckinReminderRecipientDTO]:
        """Find currently subscribed participants without a response or reminder."""
        statement = (
            select(
                WeeklyCheckin.id.label("checkin_id"),
                User.id.label("user_id"),
                User.telegram_id,
                User.first_name,
                UserGoal.text.label("goal_text"),
            )
            .join(User, User.id == WeeklyCheckin.user_id)
            .outerjoin(UserGoal, UserGoal.id == WeeklyCheckin.goal_id)
            .outerjoin(UserNotificationPreference, UserNotificationPreference.user_id == User.id)
            .where(
                WeeklyCheckin.week_start == week_start,
                WeeklyCheckin.prompted_at.is_not(None),
                WeeklyCheckin.reminded_at.is_(None),
                WeeklyCheckin.answered_at.is_(None),
                User.bot_is_blocked.is_(False),
                or_(
                    UserNotificationPreference.id.is_(None),
                    UserNotificationPreference.checkins_enabled.is_(True),
                ),
            )
            .order_by(User.id)
        )
        result = await db.execute(statement)
        return tuple(
            CheckinReminderRecipientDTO(
                checkin_id=row.checkin_id,
                user_id=row.user_id,
                telegram_id=row.telegram_id,
                first_name=row.first_name,
                goal_text=row.goal_text,
            )
            for row in result.all()
        )

    async def mark_reminder_sent(self, db: AsyncSession, *, checkin_id: int, now: datetime) -> bool:
        """Claim the reminder before delivery so concurrent workers cannot duplicate it."""
        statement = (
            update(WeeklyCheckin)
            .where(WeeklyCheckin.id == checkin_id, WeeklyCheckin.reminded_at.is_(None))
            .values(reminded_at=now)
            .returning(WeeklyCheckin.id)
        )
        return await db.scalar(statement) is not None

    async def release_reminder_claim(self, db: AsyncSession, *, checkin_id: int) -> None:
        """Release a reminder claim after a retryable Telegram failure."""
        await db.execute(update(WeeklyCheckin).where(WeeklyCheckin.id == checkin_id).values(reminded_at=None))

    async def find_campaign_rows(
        self,
        db: AsyncSession,
        *,
        week_start: date,
    ) -> Sequence[WeeklyCheckinReportRowDTO]:
        """Return delivered campaign rows with the participant's goal snapshot."""
        statement = (
            select(
                WeeklyCheckin.id,
                User.id.label("user_id"),
                User.first_name,
                User.last_name,
                User.telegram_username,
                UserGoal.text.label("goal_text"),
                WeeklyCheckin.summary,
                WeeklyCheckin.status,
                WeeklyCheckin.help_topic,
                WeeklyCheckin.prompted_at,
                WeeklyCheckin.answered_at,
            )
            .join(User, User.id == WeeklyCheckin.user_id)
            .outerjoin(UserGoal, UserGoal.id == WeeklyCheckin.goal_id)
            .where(
                WeeklyCheckin.week_start == week_start,
                WeeklyCheckin.prompted_at.is_not(None),
            )
            .order_by(User.first_name, User.last_name, User.id)
        )
        result = await db.execute(statement)
        return tuple(WeeklyCheckinReportRowDTO(**row._asdict()) for row in result.all())

    async def find_consecutive_missed_weeks(
        self,
        db: AsyncSession,
        *,
        user_ids: Sequence[int],
        current_week_start: date,
    ) -> dict[int, int]:
        """Count each participant's consecutive unanswered prompted campaigns."""
        if not user_ids:
            return {}
        statement = select(WeeklyCheckin.user_id, WeeklyCheckin.week_start, WeeklyCheckin.answered_at).where(
            WeeklyCheckin.user_id.in_(user_ids),
            WeeklyCheckin.week_start <= current_week_start,
            WeeklyCheckin.prompted_at.is_not(None),
        )
        result = await db.execute(statement)
        answers_by_user: dict[int, dict[date, datetime | None]] = {}
        for user_id, week_start, answered_at in result.all():
            answers_by_user.setdefault(user_id, {})[week_start] = answered_at

        missed_weeks_by_user: dict[int, int] = {}
        for user_id in user_ids:
            user_answers = answers_by_user.get(user_id, {})
            week_start = current_week_start
            missed_weeks = 0
            while week_start in user_answers and user_answers[week_start] is None:
                missed_weeks += 1
                week_start -= timedelta(days=7)
            missed_weeks_by_user[user_id] = missed_weeks
        return missed_weeks_by_user

    async def save_response(
        self,
        db: AsyncSession,
        *,
        checkin_id: int,
        response: ParsedCheckinResponseDTO,
        answered_at: datetime,
    ) -> None:
        """Replace parsed fields without persisting the participant's source text."""
        await db.execute(
            update(WeeklyCheckin)
            .where(WeeklyCheckin.id == checkin_id)
            .values(
                summary=response.summary,
                status=response.status.value,
                help_topic=response.help_topic,
                answered_at=answered_at,
            )
        )

    async def find_latest_answer_dates(
        self,
        db: AsyncSession,
        *,
        user_ids: Sequence[int],
        before_week_start: date,
    ) -> dict[int, datetime]:
        """Return each participant's latest check-in answer before a report campaign."""
        if not user_ids:
            return {}
        result = await db.execute(
            select(WeeklyCheckin.user_id, func.max(WeeklyCheckin.answered_at))
            .where(
                WeeklyCheckin.user_id.in_(user_ids),
                WeeklyCheckin.answered_at.is_not(None),
                WeeklyCheckin.week_start < before_week_start,
            )
            .group_by(WeeklyCheckin.user_id)
        )
        return {user_id: answered_at for user_id, answered_at in result.all() if answered_at is not None}

    async def find_report_recipients(self, db: AsyncSession) -> Sequence[PulseReportRecipientDTO]:
        """Return available administrators for a private PDF delivery."""
        statement = (
            select(User.id, User.telegram_id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.name == RoleEnum.ADMIN.value, User.bot_is_blocked.is_(False))
            .order_by(User.id)
        )
        result = await db.execute(statement)
        return tuple(
            PulseReportRecipientDTO(user_id=user_id, telegram_id=telegram_id) for user_id, telegram_id in result.all()
        )
