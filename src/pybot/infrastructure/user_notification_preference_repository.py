"""Persistence queries for per-user community notification switches."""

from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from ..db.models import UserNotificationPreference
from ..domain.services.community_checkins import NotificationPreferenceKey
from ..dto.community_checkins import UserNotificationPreferencesDTO


class UserNotificationPreferenceRepository:
    """Read defaults and update a participant's notification preferences atomically."""

    async def find_preferences(self, db: AsyncSession, user_id: int) -> UserNotificationPreferencesDTO:
        """Return all-enabled defaults until the participant changes a switch."""
        row = await db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id))
        if row is None:
            return UserNotificationPreferencesDTO()
        return UserNotificationPreferencesDTO(
            checkins_enabled=row.checkins_enabled,
            monthly_summary_enabled=row.monthly_summary_enabled,
            achievement_notifications_enabled=row.achievement_notifications_enabled,
            streak_warnings_enabled=row.streak_warnings_enabled,
            meeting_invitations_enabled=row.meeting_invitations_enabled,
        )

    async def toggle(self, db: AsyncSession, *, user_id: int, setting: NotificationPreferenceKey) -> bool:
        """Toggle one setting, treating a missing row as all enabled."""
        match setting:
            case NotificationPreferenceKey.CHECKINS:
                return await self._toggle_column(
                    db, user_id, "checkins_enabled", UserNotificationPreference.checkins_enabled
                )
            case NotificationPreferenceKey.MONTHLY_SUMMARY:
                return await self._toggle_column(
                    db, user_id, "monthly_summary_enabled", UserNotificationPreference.monthly_summary_enabled
                )
            case NotificationPreferenceKey.ACHIEVEMENTS:
                return await self._toggle_column(
                    db,
                    user_id,
                    "achievement_notifications_enabled",
                    UserNotificationPreference.achievement_notifications_enabled,
                )
            case NotificationPreferenceKey.STREAK_WARNINGS:
                return await self._toggle_column(
                    db, user_id, "streak_warnings_enabled", UserNotificationPreference.streak_warnings_enabled
                )
            case NotificationPreferenceKey.MEETING_INVITATIONS:
                return await self._toggle_column(
                    db, user_id, "meeting_invitations_enabled", UserNotificationPreference.meeting_invitations_enabled
                )
        raise AssertionError(f"Unsupported notification setting: {setting}")

    @staticmethod
    async def _toggle_column(
        db: AsyncSession,
        user_id: int,
        column_name: str,
        column: InstrumentedAttribute[bool],
    ) -> bool:
        statement = (
            insert(UserNotificationPreference)
            .values(user_id=user_id, **{column_name: False})
            .on_conflict_do_update(
                index_elements=[UserNotificationPreference.user_id],
                set_={column_name: ~column},
            )
            .returning(column)
        )
        return bool(await db.scalar(statement))

    async def set_checkins_enabled(self, db: AsyncSession, *, user_id: int, is_enabled: bool) -> bool:
        """Set the check-in subscription explicitly and return its persisted value."""
        statement = (
            insert(UserNotificationPreference)
            .values(user_id=user_id, checkins_enabled=is_enabled)
            .on_conflict_do_update(
                index_elements=[UserNotificationPreference.user_id],
                set_={"checkins_enabled": is_enabled},
            )
            .returning(UserNotificationPreference.checkins_enabled)
        )
        return bool(await db.scalar(statement))

    async def claim_streak_warning(self, db: AsyncSession, *, user_id: int, week_start: date) -> bool:
        """Claim one enabled streak warning per campaign week across concurrent workers."""
        preference = UserNotificationPreference
        statement = (
            insert(preference)
            .values(user_id=user_id, last_streak_warning_week_start=week_start)
            .on_conflict_do_update(
                index_elements=[preference.user_id],
                set_={"last_streak_warning_week_start": week_start},
                where=and_(
                    preference.streak_warnings_enabled.is_(True),
                    or_(
                        preference.last_streak_warning_week_start.is_(None),
                        preference.last_streak_warning_week_start != week_start,
                    ),
                ),
            )
            .returning(preference.user_id)
        )
        return await db.scalar(statement) is not None

    async def release_streak_warning_claim(self, db: AsyncSession, *, user_id: int, week_start: date) -> None:
        """Release a weekly streak warning claim after a retryable delivery failure."""
        row = await db.scalar(
            select(UserNotificationPreference).where(
                UserNotificationPreference.user_id == user_id,
                UserNotificationPreference.last_streak_warning_week_start == week_start,
            )
        )
        if row is not None:
            row.last_streak_warning_week_start = None
