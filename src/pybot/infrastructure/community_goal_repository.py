"""Persistence operations for participant goal history."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User, UserGoal
from ..domain.services.community_checkins import GoalStatus


class CommunityGoalRepository:
    """Serialize goal changes and keep one active goal per participant."""

    async def find_active_goal(self, db: AsyncSession, user_id: int) -> UserGoal | None:
        """Find the participant's current goal, if any."""
        return await db.scalar(
            select(UserGoal).where(UserGoal.user_id == user_id, UserGoal.status == GoalStatus.ACTIVE.value)
        )

    async def set_active_goal(self, db: AsyncSession, *, user_id: int, text: str) -> UserGoal:
        """Cancel the previous goal and append a new active goal atomically."""
        await self._lock_user(db, user_id)
        active_goal = await db.scalar(
            select(UserGoal)
            .where(UserGoal.user_id == user_id, UserGoal.status == GoalStatus.ACTIVE.value)
            .with_for_update()
        )
        if active_goal is not None:
            active_goal.status = GoalStatus.CANCELLED.value
            active_goal.updated_at = func.now()

        goal = UserGoal(user_id=user_id, text=text, status=GoalStatus.ACTIVE.value)
        db.add(goal)
        await db.flush()
        return goal

    async def mark_active_goal_achieved(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        achieved_at: datetime,
    ) -> UserGoal | None:
        """Mark the current goal achieved and return it; return None when absent."""
        await self._lock_user(db, user_id)
        goal = await db.scalar(
            select(UserGoal)
            .where(UserGoal.user_id == user_id, UserGoal.status == GoalStatus.ACTIVE.value)
            .with_for_update()
        )
        if goal is None:
            return None
        goal.status = GoalStatus.ACHIEVED.value
        goal.achieved_at = achieved_at
        goal.updated_at = achieved_at
        await db.flush()
        return goal

    @staticmethod
    async def _lock_user(db: AsyncSession, user_id: int) -> None:
        await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
