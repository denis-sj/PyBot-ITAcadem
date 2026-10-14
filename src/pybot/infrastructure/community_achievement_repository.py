"""Persistence queries for community achievement awards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User, UserAchievementAward
from ..dto import AchievementAwardReadDTO, AchievementPublicationDTO


class CommunityAchievementRepository:
    """Stateless repository for the user's one-time community awards."""

    async def add_missing_awards(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        awards: Mapping[str, datetime],
        silent: bool = False,
    ) -> int:
        """Insert awards once, tolerating concurrent recalculations."""
        if not awards:
            return 0

        values = [
            {
                "user_id": user_id,
                "code": code,
                "achieved_at": achieved_at,
                "is_published": silent or code == "first_step",
                "is_notified": silent,
            }
            for code, achieved_at in awards.items()
        ]
        statement = (
            insert(UserAchievementAward)
            .values(values)
            .on_conflict_do_nothing(index_elements=[UserAchievementAward.user_id, UserAchievementAward.code])
            .returning(UserAchievementAward.id)
        )
        result = await db.execute(statement)
        return len(result.scalars().all())

    async def find_user_awards(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Sequence[AchievementAwardReadDTO]:
        """Return all stored awards for a user in acquisition order."""
        statement = (
            select(UserAchievementAward.code, UserAchievementAward.achieved_at)
            .where(UserAchievementAward.user_id == user_id)
            .order_by(UserAchievementAward.achieved_at, UserAchievementAward.code)
        )
        result = await db.execute(statement)
        return [AchievementAwardReadDTO(code=code, achieved_at=achieved_at) for code, achieved_at in result.all()]

    async def find_unnotified_awards(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> Sequence[UserAchievementAward]:
        """Return outstanding personal congratulations for a user."""
        statement = (
            select(UserAchievementAward)
            .where(
                UserAchievementAward.user_id == user_id,
                UserAchievementAward.is_notified.is_(False),
            )
            .with_for_update(skip_locked=True)
            .order_by(UserAchievementAward.achieved_at, UserAchievementAward.code)
        )
        result = await db.execute(statement)
        return result.scalars().all()

    async def find_unpublished_public_awards(self, db: AsyncSession) -> Sequence[AchievementPublicationDTO]:
        """Return pending awards eligible for the daily group digest."""
        statement = (
            select(
                UserAchievementAward.id,
                UserAchievementAward.user_id,
                User.first_name,
                User.last_name,
                User.telegram_username,
                UserAchievementAward.code,
                UserAchievementAward.achieved_at,
            )
            .join(User, User.id == UserAchievementAward.user_id)
            .where(
                UserAchievementAward.is_published.is_(False),
                UserAchievementAward.code.not_in(("first_step", "top_week")),
            )
            .order_by(UserAchievementAward.achieved_at, UserAchievementAward.id)
        )
        result = await db.execute(statement)
        return [AchievementPublicationDTO(**row._asdict()) for row in result.all()]

    async def find_awards_in_period(
        self,
        db: AsyncSession,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> Sequence[AchievementPublicationDTO]:
        """Return all earned achievement badges in a half-open report interval."""
        statement = (
            select(
                UserAchievementAward.id,
                UserAchievementAward.user_id,
                User.first_name,
                User.last_name,
                User.telegram_username,
                UserAchievementAward.code,
                UserAchievementAward.achieved_at,
            )
            .join(User, User.id == UserAchievementAward.user_id)
            .where(
                UserAchievementAward.achieved_at >= start_at,
                UserAchievementAward.achieved_at < end_at,
            )
            .order_by(UserAchievementAward.achieved_at, UserAchievementAward.id)
        )
        result = await db.execute(statement)
        return tuple(AchievementPublicationDTO(**row._asdict()) for row in result.all())

    async def mark_notified(self, db: AsyncSession, award_ids: Sequence[int]) -> None:
        """Mark awards whose congratulations were delivered or skipped permanently."""
        if not award_ids:
            return
        await db.execute(
            update(UserAchievementAward).where(UserAchievementAward.id.in_(award_ids)).values(is_notified=True)
        )

    async def mark_all_user_awards_notified(self, db: AsyncSession, user_id: int) -> None:
        """Consume pending personal awards when the participant opts out of congratulations."""
        await db.execute(
            update(UserAchievementAward)
            .where(UserAchievementAward.user_id == user_id, UserAchievementAward.is_notified.is_(False))
            .values(is_notified=True)
        )

    async def mark_published(self, db: AsyncSession, award_ids: Sequence[int]) -> None:
        """Mark awards included in a successfully delivered digest."""
        if not award_ids:
            return
        await db.execute(
            update(UserAchievementAward).where(UserAchievementAward.id.in_(award_ids)).values(is_published=True)
        )

    async def mark_user_awards_published(
        self,
        db: AsyncSession,
        user_ids: Sequence[int],
        code: str,
    ) -> None:
        """Mark one award code as published for a set of participants."""
        if not user_ids:
            return
        await db.execute(
            update(UserAchievementAward)
            .where(
                UserAchievementAward.user_id.in_(user_ids),
                UserAchievementAward.code == code,
            )
            .values(is_published=True)
        )
