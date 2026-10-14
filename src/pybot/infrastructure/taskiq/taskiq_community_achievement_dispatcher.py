"""TaskIQ adapter for post-commit community achievement recalculations."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...services.ports import CommunityAchievementDispatchPort

if TYPE_CHECKING:
    from taskiq import AsyncTaskiqDecoratedTask


class TaskIQCommunityAchievementDispatcher(CommunityAchievementDispatchPort):
    """Queue recalculation jobs without waiting on attendance/role handlers."""

    def __init__(
        self,
        task_resolver: Callable[[], AsyncTaskiqDecoratedTask[..., Any]] | None = None,
    ) -> None:
        self._task_resolver = task_resolver or self._resolve_task

    @staticmethod
    def _resolve_task() -> AsyncTaskiqDecoratedTask[..., Any]:
        from .taskiq_app import get_taskiq_achievement_recalculation_task  # noqa: PLC0415

        return get_taskiq_achievement_recalculation_task()

    async def recalculate_user_achievements(self, user_id: int) -> None:
        """Queue recalculation for the participant after the primary action commits."""
        await self._task_resolver().kiq(user_id=user_id)
