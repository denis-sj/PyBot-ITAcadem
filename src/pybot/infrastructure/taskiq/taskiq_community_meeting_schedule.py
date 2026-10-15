"""Idempotent TaskIQ schedule registration for meeting reminders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core import logger

if TYPE_CHECKING:
    from taskiq.kicker import AsyncKicker
    from taskiq.scheduler.scheduled_task import ScheduledTask
    from taskiq_redis import ListRedisScheduleSource

MEETING_MAINTENANCE_SCHEDULE_ID = "community_meetings:minute_maintenance"
MEETING_MAINTENANCE_TASK_NAME = "community_meetings.process_due_reminders"
MEETING_MAINTENANCE_CRON = "* * * * *"
MEETING_TIMEZONE = "Asia/Yekaterinburg"


@dataclass(frozen=True, slots=True)
class MeetingScheduleSpec:
    """Stable identity and cadence for meeting reminders and status cleanup."""

    schedule_id: str = MEETING_MAINTENANCE_SCHEDULE_ID
    task_name: str = MEETING_MAINTENANCE_TASK_NAME
    cron: str = MEETING_MAINTENANCE_CRON


def is_expected_meeting_schedule(schedule: ScheduledTask, spec: MeetingScheduleSpec) -> bool:
    return (
        schedule.task_name == spec.task_name
        and schedule.cron == spec.cron
        and str(schedule.cron_offset) == MEETING_TIMEZONE
    )


async def ensure_community_meeting_schedule(
    *,
    source: ListRedisScheduleSource,
    resolve_kicker: Callable[[], AsyncKicker[Any, Any]],
) -> None:
    """Ensure one minute-level schedule exists and is aligned to Yekaterinburg."""
    spec = MeetingScheduleSpec()
    schedules = await source.get_schedules()
    existing = next((item for item in schedules if item.schedule_id == spec.schedule_id), None)
    if existing is not None and is_expected_meeting_schedule(existing, spec):
        return
    if existing is not None:
        await source.delete_schedule(spec.schedule_id)
    await (
        resolve_kicker()
        .with_schedule_id(spec.schedule_id)
        .with_labels(cron_offset=MEETING_TIMEZONE)
        .schedule_by_cron(source, spec.cron)
    )
    logger.info(
        "событие=community_meeting_schedule status=created schedule_id={schedule_id} cron={cron}",
        schedule_id=spec.schedule_id,
        cron=spec.cron,
    )
