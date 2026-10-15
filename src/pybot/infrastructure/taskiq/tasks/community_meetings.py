"""TaskIQ job for meeting reminders and automatic completion status updates."""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core.config import AppSettings
from ....services.community_meetings import CommunityMeetingService

MEETING_MAINTENANCE_TASK_NAME = "community_meetings.process_due_reminders"


async def process_due_meeting_reminders_task(service: FromDishka[CommunityMeetingService]) -> int:
    """Deliver due reminders and mark meetings finished after their grace period."""
    return await service.process_due_reminders()


def register_tasks(*, broker: AsyncBroker, settings: AppSettings) -> AsyncTaskiqDecoratedTask[..., Any]:
    """Register the minute-level meeting maintenance job with standard retry settings."""
    return broker.task(
        task_name=MEETING_MAINTENANCE_TASK_NAME,
        retry_on_error=settings.leaderboard_weekly_retry_enabled,
        max_retries=settings.leaderboard_weekly_retry_max_retries,
        delay=settings.leaderboard_weekly_retry_delay_s,
    )(inject(patch_module=True)(process_due_meeting_reminders_task))
