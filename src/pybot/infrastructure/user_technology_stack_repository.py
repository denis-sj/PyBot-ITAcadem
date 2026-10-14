"""Persistence queries for participants' self-reported technology stacks."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AttendanceRecord, Competence, Role, User, UserCompetence, UserRole, UserTechnologyStack
from ..domain.exceptions import UserNotFoundError
from ..domain.services.technology_stack import (
    MAX_TECHNOLOGY_STACK_SIZE,
    TechnologyLevel,
    TechnologyStackItem,
    TechnologyStackValidationError,
)
from ..dto.community_checkins import PulseTechnologyDTO
from ..dto.technology_stack import (
    TechnologyStackHelperDTO,
    TechnologyStackMentorCompetenceDTO,
    TechnologyStackReadDTO,
)


class UserTechnologyStackRepository:
    """Stateless database access for stack entries and potential helpers."""

    async def find_user_stack(self, db: AsyncSession, user_id: int) -> Sequence[TechnologyStackReadDTO]:
        statement = (
            select(UserTechnologyStack)
            .where(UserTechnologyStack.user_id == user_id)
            .order_by(func.lower(UserTechnologyStack.technology), UserTechnologyStack.technology)
        )
        result = await db.execute(statement)
        return [
            TechnologyStackReadDTO(
                technology=item.technology,
                level=TechnologyLevel(item.level),
                updated_at=item.updated_at,
            )
            for item in result.scalars().all()
        ]

    async def replace_user_stack(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        items: Sequence[TechnologyStackItem],
    ) -> None:
        """Replace the full stack while serializing edits for the same user."""
        await self._lock_user(db, user_id)
        await db.execute(delete(UserTechnologyStack).where(UserTechnologyStack.user_id == user_id))
        db.add_all(
            UserTechnologyStack(user_id=user_id, technology=item.technology, level=item.level.value) for item in items
        )
        await db.flush()

    async def set_user_stack_item(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        item: TechnologyStackItem,
    ) -> bool:
        """Insert or update one normalized item and report whether it was new."""
        await self._lock_user(db, user_id)
        existing = await db.scalar(
            select(UserTechnologyStack)
            .where(
                UserTechnologyStack.user_id == user_id,
                func.lower(UserTechnologyStack.technology) == item.technology.casefold(),
            )
            .with_for_update()
        )
        if existing is not None:
            await db.execute(
                update(UserTechnologyStack)
                .where(UserTechnologyStack.id == existing.id)
                .values(technology=item.technology, level=item.level.value, updated_at=func.now())
            )
            return False

        count = await db.scalar(
            select(func.count()).select_from(UserTechnologyStack).where(UserTechnologyStack.user_id == user_id)
        )
        if count is not None and count >= MAX_TECHNOLOGY_STACK_SIZE:
            raise TechnologyStackValidationError("В стеке может быть не больше 20 технологий.")

        db.add(UserTechnologyStack(user_id=user_id, technology=item.technology, level=item.level.value))
        await db.flush()
        return True

    async def remove_user_stack_item(self, db: AsyncSession, *, user_id: int, technology: str) -> bool:
        """Remove one item after normalizing its canonical name in the service."""
        await self._lock_user(db, user_id)
        item_id = await db.scalar(
            select(UserTechnologyStack.id)
            .where(
                UserTechnologyStack.user_id == user_id,
                func.lower(UserTechnologyStack.technology) == technology.casefold(),
            )
            .with_for_update()
        )
        if item_id is None:
            return False
        await db.execute(delete(UserTechnologyStack).where(UserTechnologyStack.id == item_id))
        return True

    async def has_user_stack_item(self, db: AsyncSession, *, user_id: int, technology: str) -> bool:
        statement = select(UserTechnologyStack.id).where(
            UserTechnologyStack.user_id == user_id,
            func.lower(UserTechnologyStack.technology) == technology.casefold(),
        )
        return await db.scalar(statement) is not None

    async def user_stack_size(self, db: AsyncSession, user_id: int) -> int:
        count = await db.scalar(
            select(func.count()).select_from(UserTechnologyStack).where(UserTechnologyStack.user_id == user_id)
        )
        return count or 0

    async def find_top_learning_technologies(
        self,
        db: AsyncSession,
        *,
        limit: int,
    ) -> Sequence[PulseTechnologyDTO]:
        """Aggregate the most common self-reported learning technologies."""
        statement = (
            select(UserTechnologyStack.technology, func.count(func.distinct(UserTechnologyStack.user_id)))
            .where(UserTechnologyStack.level == TechnologyLevel.LEARNING.value)
            .group_by(UserTechnologyStack.technology)
            .order_by(
                func.count(func.distinct(UserTechnologyStack.user_id)).desc(),
                func.lower(UserTechnologyStack.technology),
            )
            .limit(limit)
        )
        result = await db.execute(statement)
        return tuple(
            PulseTechnologyDTO(technology=technology, participant_count=participant_count)
            for technology, participant_count in result.all()
        )

    async def find_mentor_competences(self, db: AsyncSession) -> Sequence[TechnologyStackMentorCompetenceDTO]:
        """Return visible mentor competence names and most recent attendance dates."""
        statement = (
            select(
                User.id,
                User.first_name,
                User.last_name,
                User.telegram_username,
                Competence.name,
                func.max(AttendanceRecord.date),
            )
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .join(UserCompetence, UserCompetence.user_id == User.id)
            .join(Competence, Competence.id == UserCompetence.competence_id)
            .outerjoin(AttendanceRecord, AttendanceRecord.user_id == User.id)
            .where(Role.name == "Mentor", User.bot_is_blocked.is_(False))
            .group_by(User.id, User.first_name, User.last_name, User.telegram_username, Competence.name)
        )
        result = await db.execute(statement)
        return [
            TechnologyStackMentorCompetenceDTO(
                helper=TechnologyStackHelperDTO(
                    user_id=user_id,
                    first_name=first_name,
                    last_name=last_name,
                    telegram_username=telegram_username,
                    is_mentor=True,
                    last_visit=last_visit,
                ),
                competence_name=competence_name,
            )
            for user_id, first_name, last_name, telegram_username, competence_name, last_visit in result.all()
        ]

    async def find_known_technology_helpers(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        technology: str,
    ) -> Sequence[TechnologyStackHelperDTO]:
        """Return unblocked participants who marked the canonical technology as known."""
        statement = (
            select(
                User.id,
                User.first_name,
                User.last_name,
                User.telegram_username,
                func.max(AttendanceRecord.date),
            )
            .join(UserTechnologyStack, UserTechnologyStack.user_id == User.id)
            .outerjoin(AttendanceRecord, AttendanceRecord.user_id == User.id)
            .where(
                UserTechnologyStack.level == TechnologyLevel.KNOW.value,
                func.lower(UserTechnologyStack.technology) == technology.casefold(),
                User.id != user_id,
                User.bot_is_blocked.is_(False),
            )
            .group_by(User.id, User.first_name, User.last_name, User.telegram_username)
        )
        result = await db.execute(statement)
        return [
            TechnologyStackHelperDTO(
                user_id=candidate_id,
                first_name=first_name,
                last_name=last_name,
                telegram_username=telegram_username,
                is_mentor=False,
                last_visit=last_visit,
            )
            for candidate_id, first_name, last_name, telegram_username, last_visit in result.all()
        ]

    @staticmethod
    async def _lock_user(db: AsyncSession, user_id: int) -> None:
        user_id_result = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
        if user_id_result is None:
            raise UserNotFoundError(user_id=user_id)
