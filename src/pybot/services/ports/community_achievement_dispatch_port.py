"""Port for dispatching post-commit achievement recalculation work."""

from abc import ABC, abstractmethod


class CommunityAchievementDispatchPort(ABC):
    """Dispatch lightweight achievement recalculation after a committed action."""

    @abstractmethod
    async def recalculate_user_achievements(self, user_id: int) -> None:
        """Queue a recalculation for one participant."""
