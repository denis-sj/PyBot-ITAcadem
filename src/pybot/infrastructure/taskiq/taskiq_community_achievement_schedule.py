"""Idempotent TaskIQ schedule registration for community achievement posts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core import logger
from ...core.config import AppSettings

if TYPE_CHECKING:
    from taskiq.kicker import AsyncKicker
    from taskiq.scheduler.scheduled_task import ScheduledTask
    from taskiq_redis import ListRedisScheduleSource

ACHIEVEMENT_DIGEST_SCHEDULE_ID = "community_achievements:daily_digest"
TOP_WEEK_SCHEDULE_ID = "community_achievements:top_week"
ACHIEVEMENT_TIMEZONE = "Asia/Yekaterinburg"
ACHIEVEMENT_DIGEST_CRON = "0 19 * * *"
TOP_WEEK_CRON = "0 12 * * 1"


@dataclass(frozen=True, slots=True)
class AchievementScheduleSpec:
    schedule_id: str
    task_name: str
    cron: str


def is_expected_schedule(schedule: ScheduledTask, spec: AchievementScheduleSpec) -> bool:
    """Check whether a registered schedule still matches the requested task and time."""
    return (
        schedule.task_name == spec.task_name
        and schedule.cron == spec.cron
        and str(schedule.cron_offset) == ACHIEVEMENT_TIMEZONE
    )


async def ensure_community_achievement_schedules(
    *,
    source: ListRedisScheduleSource,
    settings: AppSettings,
    resolve_kicker: Callable[[str], AsyncKicker[Any, Any]],
) -> None:
    """Create or replace the digest and weekly top schedules, or remove them when disabled."""
    specs = (
        AchievementScheduleSpec(
            schedule_id=ACHIEVEMENT_DIGEST_SCHEDULE_ID,
            task_name="community_achievements.publish_daily_digest",
            cron=ACHIEVEMENT_DIGEST_CRON,
        ),
        AchievementScheduleSpec(
            schedule_id=TOP_WEEK_SCHEDULE_ID,
            task_name="community_achievements.publish_top_week",
            cron=TOP_WEEK_CRON,
        ),
    )
    existing_schedules = await source.get_schedules()

    if not settings.achievements_publication_enabled:
        for spec in specs:
            if any(item.schedule_id == spec.schedule_id for item in existing_schedules):
                await source.delete_schedule(spec.schedule_id)
        return

    for spec in specs:
        existing = next((item for item in existing_schedules if item.schedule_id == spec.schedule_id), None)
        if existing is not None and is_expected_schedule(existing, spec):
            continue
        if existing is not None:
            await source.delete_schedule(spec.schedule_id)
        await (
            resolve_kicker(spec.task_name)
            .with_schedule_id(spec.schedule_id)
            .with_labels(cron_offset=ACHIEVEMENT_TIMEZONE)
            .schedule_by_cron(source, spec.cron)
        )
        logger.info(
            "событие=community_achievement_schedule status=created schedule_id={schedule_id} cron={cron}",
            schedule_id=spec.schedule_id,
            cron=spec.cron,
        )
