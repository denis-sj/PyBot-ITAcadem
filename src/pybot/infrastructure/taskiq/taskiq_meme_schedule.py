"""Idempotent weekly meme schedule registration."""

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

MEME_SCHEDULE_ID = "weekly_meme:wednesday"
MEME_TASK_NAME = "weekly_meme.publish"
MEME_CRON = "16 16 * * 3"
MEME_TIMEZONE = "Asia/Yekaterinburg"


@dataclass(frozen=True, slots=True)
class MemeScheduleSpec:
    """Stable weekly schedule identity and cadence."""

    schedule_id: str = MEME_SCHEDULE_ID
    task_name: str = MEME_TASK_NAME
    cron: str = MEME_CRON


def is_expected_meme_schedule(schedule: ScheduledTask, spec: MemeScheduleSpec) -> bool:
    return (
        schedule.task_name == spec.task_name
        and schedule.cron == spec.cron
        and str(schedule.cron_offset) == MEME_TIMEZONE
    )


async def ensure_weekly_meme_schedule(
    *,
    source: ListRedisScheduleSource,
    settings: AppSettings,
    resolve_kicker: Callable[[], AsyncKicker[Any, Any]],
) -> None:
    """Create or replace the schedule, removing it when meme publication is disabled."""
    spec = MemeScheduleSpec()
    schedules = await source.get_schedules()
    existing = next((item for item in schedules if item.schedule_id == spec.schedule_id), None)
    if not settings.meme_publication_enabled:
        if existing is not None:
            await source.delete_schedule(spec.schedule_id)
        return
    if existing is not None and is_expected_meme_schedule(existing, spec):
        return
    if existing is not None:
        await source.delete_schedule(spec.schedule_id)
    await (
        resolve_kicker()
        .with_schedule_id(spec.schedule_id)
        .with_labels(cron_offset=MEME_TIMEZONE)
        .schedule_by_cron(source, spec.cron)
    )
    logger.info(
        "event=weekly_meme_schedule status=created schedule_id={schedule_id} cron={cron}",
        schedule_id=spec.schedule_id,
        cron=spec.cron,
    )
