"""Idempotent TaskIQ schedule registration for weekly community engagement jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core import logger

if TYPE_CHECKING:
    from taskiq.kicker import AsyncKicker
    from taskiq.scheduler.scheduled_task import ScheduledTask
    from taskiq_redis import ListRedisScheduleSource

COMMUNITY_CHECKIN_TIMEZONE = "Asia/Yekaterinburg"
COMMUNITY_CHECKIN_SCHEDULES = (
    ("community_checkins:weekly_prompt", "community_checkins.send_weekly_prompts", "0 15 * * 3"),
    ("community_checkins:weekly_reminder", "community_checkins.send_weekly_reminders", "0 11 * * 4"),
    ("community_checkins:pulse_report", "community_checkins.deliver_pulse_report", "0 15 * * 4"),
    ("community_checkins:streak_warning", "community_checkins.warn_at_risk_streaks", "0 12 * * 6"),
)


@dataclass(frozen=True, slots=True)
class CommunityCheckinScheduleSpec:
    """A stable schedule identity, task name, and weekly local cron expression."""

    schedule_id: str
    task_name: str
    cron: str


def is_expected_schedule(schedule: ScheduledTask, spec: CommunityCheckinScheduleSpec) -> bool:
    """Check whether an existing scheduler entry matches the configured job."""
    return (
        schedule.task_name == spec.task_name
        and schedule.cron == spec.cron
        and str(schedule.cron_offset) == COMMUNITY_CHECKIN_TIMEZONE
    )


async def ensure_community_checkin_schedules(
    *,
    source: ListRedisScheduleSource,
    resolve_kicker: Callable[[str], AsyncKicker[Any, Any]],
) -> None:
    """Create or update all four community schedules in the scheduler's Redis source."""
    specs = tuple(CommunityCheckinScheduleSpec(*values) for values in COMMUNITY_CHECKIN_SCHEDULES)
    existing_schedules = await source.get_schedules()
    for spec in specs:
        existing = next((item for item in existing_schedules if item.schedule_id == spec.schedule_id), None)
        if existing is not None and is_expected_schedule(existing, spec):
            continue
        if existing is not None:
            await source.delete_schedule(spec.schedule_id)
        await (
            resolve_kicker(spec.task_name)
            .with_schedule_id(spec.schedule_id)
            .with_labels(cron_offset=COMMUNITY_CHECKIN_TIMEZONE)
            .schedule_by_cron(source, spec.cron)
        )
        logger.info(
            "событие=community_checkin_schedule status=created schedule_id={schedule_id} cron={cron}",
            schedule_id=spec.schedule_id,
            cron=spec.cron,
        )
