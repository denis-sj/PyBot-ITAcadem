"""TaskIQ jobs for check-in prompts, reminders, streak warnings, and pulse reports."""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core.config import AppSettings
from ....services.community_checkins import CommunityCheckinService
from ....services.pulse_report import PulseReportService

CHECKIN_PROMPTS_TASK_NAME = "community_checkins.send_weekly_prompts"
CHECKIN_REMINDERS_TASK_NAME = "community_checkins.send_weekly_reminders"
STREAK_WARNINGS_TASK_NAME = "community_checkins.warn_at_risk_streaks"
PULSE_REPORT_TASK_NAME = "community_checkins.deliver_pulse_report"


async def send_weekly_prompts_task(service: FromDishka[CommunityCheckinService]) -> int:
    """Deliver Wednesday prompts to subscribed participants."""
    return await service.deliver_weekly_prompts()


async def send_weekly_reminders_task(service: FromDishka[CommunityCheckinService]) -> int:
    """Deliver the one Thursday reminder to check-in nonresponders."""
    return await service.send_weekly_reminders()


async def warn_at_risk_streaks_task(service: FromDishka[CommunityCheckinService]) -> int:
    """Warn subscribed participants who may lose their attendance streak."""
    return await service.warn_at_risk_streaks()


async def deliver_pulse_report_task(service: FromDishka[PulseReportService]) -> int:
    """Build and DM the fixed-template PDF report to administrators."""
    return await service.deliver_weekly_report()


def register_tasks(
    *,
    broker: AsyncBroker,
    settings: AppSettings,
) -> tuple[
    AsyncTaskiqDecoratedTask[..., Any],
    AsyncTaskiqDecoratedTask[..., Any],
    AsyncTaskiqDecoratedTask[..., Any],
    AsyncTaskiqDecoratedTask[..., Any],
]:
    """Register community check-in tasks with the configured transient-failure retry policy."""
    retry_kwargs: dict[str, Any] = {
        "retry_on_error": settings.leaderboard_weekly_retry_enabled,
        "max_retries": settings.leaderboard_weekly_retry_max_retries,
        "delay": settings.leaderboard_weekly_retry_delay_s,
    }
    return (
        broker.task(task_name=CHECKIN_PROMPTS_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(send_weekly_prompts_task)
        ),
        broker.task(task_name=CHECKIN_REMINDERS_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(send_weekly_reminders_task)
        ),
        broker.task(task_name=STREAK_WARNINGS_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(warn_at_risk_streaks_task)
        ),
        broker.task(task_name=PULSE_REPORT_TASK_NAME, **retry_kwargs)(
            inject(patch_module=True)(deliver_pulse_report_task)
        ),
    )
