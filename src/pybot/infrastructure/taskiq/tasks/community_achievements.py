"""TaskIQ jobs for community achievement awards and publications."""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core.config import AppSettings
from ....services.community_achievements import CommunityAchievementService

RECALCULATE_ACHIEVEMENTS_TASK_NAME = "community_achievements.recalculate_user"
PUBLISH_ACHIEVEMENT_DIGEST_TASK_NAME = "community_achievements.publish_daily_digest"
PUBLISH_TOP_WEEK_TASK_NAME = "community_achievements.publish_top_week"


async def recalculate_user_achievements_task(
    user_id: int,
    service: FromDishka[CommunityAchievementService],
) -> int:
    """Recalculate and deliver any new awards for one participant."""
    return await service.recalculate_user(user_id)


async def publish_daily_achievement_digest_task(
    service: FromDishka[CommunityAchievementService],
) -> int:
    """Publish the daily public achievement digest when it has new awards."""
    return await service.publish_daily_digest()


async def publish_top_week_task(
    service: FromDishka[CommunityAchievementService],
) -> int:
    """Award and publish the top three attendance participants from last week."""
    return await service.publish_top_week()


def register_tasks(
    *,
    broker: AsyncBroker,
    settings: AppSettings,
) -> tuple[
    AsyncTaskiqDecoratedTask[..., Any],
    AsyncTaskiqDecoratedTask[..., Any],
    AsyncTaskiqDecoratedTask[..., Any],
]:
    """Register achievement recalculation and publication jobs."""
    retry_kwargs: dict[str, Any] = {
        "retry_on_error": settings.leaderboard_weekly_retry_enabled,
        "max_retries": settings.leaderboard_weekly_retry_max_retries,
        "delay": settings.leaderboard_weekly_retry_delay_s,
    }
    return (
        broker.task(task_name=RECALCULATE_ACHIEVEMENTS_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(recalculate_user_achievements_task)
        ),
        broker.task(task_name=PUBLISH_ACHIEVEMENT_DIGEST_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(publish_daily_achievement_digest_task)
        ),
        broker.task(task_name=PUBLISH_TOP_WEEK_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(publish_top_week_task)
        ),
    )
