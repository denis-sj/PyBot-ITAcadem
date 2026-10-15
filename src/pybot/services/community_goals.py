"""Application use cases for setting and completing participant goals."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import YEKATERINBURG_TZ
from ..db.models import UserGoal
from ..domain.services.community_achievements import AchievementCode
from ..domain.services.community_checkins import GoalStatus, normalize_goal_text
from ..dto.community_checkins import CommunityGoalReadDTO
from ..infrastructure.community_goal_repository import CommunityGoalRepository
from .community_achievements import CommunityAchievementService


class CommunityGoalService:
    """Keep goal history and award the durable goal-completed badge."""

    def __init__(
        self,
        db: AsyncSession,
        repository: CommunityGoalRepository,
        achievements: CommunityAchievementService,
    ) -> None:
        self._db = db
        self._repository = repository
        self._achievements = achievements

    async def get_active_goal(self, user_id: int) -> CommunityGoalReadDTO | None:
        """Return the participant's active goal, if one exists."""
        goal = await self._repository.find_active_goal(self._db, user_id)
        return self._to_dto(goal) if goal is not None else None

    async def set_goal(self, user_id: int, text: str) -> CommunityGoalReadDTO:
        """Cancel the previous active goal and create a normalized replacement."""
        normalized_text = normalize_goal_text(text)
        goal = await self._repository.set_active_goal(self._db, user_id=user_id, text=normalized_text)
        await self._db.commit()
        return self._to_dto(goal)

    async def complete_active_goal(self, user_id: int) -> CommunityGoalReadDTO | None:
        """Mark the active goal achieved and grant its one-time community badge."""
        achieved_at = datetime.now(YEKATERINBURG_TZ)
        goal = await self._repository.mark_active_goal_achieved(
            self._db,
            user_id=user_id,
            achieved_at=achieved_at,
        )
        if goal is None:
            return None
        await self._db.commit()
        await self._achievements.award_event_achievement(
            user_id,
            code=AchievementCode.GOAL_DONE,
            achieved_at=achieved_at,
        )
        return self._to_dto(goal)

    @staticmethod
    def _to_dto(goal: UserGoal) -> CommunityGoalReadDTO:
        return CommunityGoalReadDTO(
            id=goal.id,
            text=goal.text,
            status=GoalStatus(goal.status),
            created_at=goal.created_at,
            achieved_at=goal.achieved_at,
        )
