"""TaskIQ publication task for the weekly Lemmy meme."""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core.config import AppSettings
from ....services.meme_of_the_week import MemeOfTheWeekService

PUBLISH_WEEKLY_MEME_TASK_NAME = "weekly_meme.publish"


async def publish_weekly_meme_task(service: FromDishka[MemeOfTheWeekService]) -> str:
    """Run the idempotent weekly publication use case."""
    result = await service.publish_weekly()
    return result.status.value


def register_tasks(*, broker: AsyncBroker, settings: AppSettings) -> AsyncTaskiqDecoratedTask[..., Any]:
    """Register the weekly publication job with the standard TaskIQ retry policy."""
    return broker.task(
        task_name=PUBLISH_WEEKLY_MEME_TASK_NAME,
        retry_on_error=settings.leaderboard_weekly_retry_enabled,
        max_retries=settings.leaderboard_weekly_retry_max_retries,
        delay=settings.leaderboard_weekly_retry_delay_s,
    )(inject(patch_module=True)(publish_weekly_meme_task))
