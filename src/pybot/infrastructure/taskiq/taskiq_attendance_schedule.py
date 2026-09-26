from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core import logger

if TYPE_CHECKING:
    from taskiq.kicker import AsyncKicker
    from taskiq.scheduler.scheduled_task import ScheduledTask
    from taskiq_redis import ListRedisScheduleSource

ATTENDANCE_CLEANUP_SCHEDULE_ID = "attendance:cleanup_stale_drafts"
ATTENDANCE_CLEANUP_TASK_NAME = "attendance.cleanup_stale_drafts"


@dataclass(slots=True)
class AttendanceCleanupScheduleSpec:
    """Спецификация для периодической очистки просроченных черновиков посещаемости.

    Определяет параметры расписания для задачи очистки.
    """

    cron_expression: str

    schedule_id: str = ATTENDANCE_CLEANUP_SCHEDULE_ID
    task_name: str = ATTENDANCE_CLEANUP_TASK_NAME


def is_expected_attendance_cleanup_schedule(
    schedule: ScheduledTask,
    *,
    spec: AttendanceCleanupScheduleSpec,
) -> bool:
    """Проверяет, соответствует ли существующее расписание TaskIQ заданной спецификации.

    Args:
        schedule: Проверяемое расписание из TaskIQ.
        spec: Спецификация, которой должно соответствовать расписание.

    Returns:
        bool: True, если расписание соответствует спецификации, иначе False.
    """
    if schedule.task_name != spec.task_name:
        return False
    return schedule.cron == spec.cron_expression


async def ensure_attendance_cleanup_schedule(
    *,
    source: ListRedisScheduleSource,
    spec: AttendanceCleanupScheduleSpec,
    resolve_kicker: Callable[[], AsyncKicker[Any, Any]] | None = None,
) -> None:
    """Идемпотентно гарантирует наличие расписания очистки черновиков в Redis.

    Если расписание отсутствует или не соответствует спецификации, оно будет создано или перезаписано.

    Args:
        source: Источник расписаний Redis.
        spec: Спецификация расписания.
        resolve_kicker: Функция для получения объекта Kicker для постановки задачи.

    Raises:
        RuntimeError: Если kicker не сконфигурирован.
    """
    if resolve_kicker is None:
        raise RuntimeError("Attendance cleanup kicker resolver is not configured.")

    existing_schedule = next(
        (item for item in await source.get_schedules() if item.schedule_id == spec.schedule_id), None
    )

    if existing_schedule is not None and is_expected_attendance_cleanup_schedule(
        existing_schedule,
        spec=spec,
    ):
        logger.info(
            "событие=attendance_cleanup_ensure status=up_to_date schedule_id={schedule_id}",
            schedule_id=spec.schedule_id,
        )
        return

    if existing_schedule is not None:
        await source.delete_schedule(spec.schedule_id)
        logger.info(
            "событие=attendance_cleanup_ensure status=replaced schedule_id={schedule_id}",
            schedule_id=spec.schedule_id,
        )

    await (
        resolve_kicker()
        .with_schedule_id(spec.schedule_id)
        .schedule_by_cron(
            source,
            spec.cron_expression,
        )
    )
    logger.info(
        "событие=attendance_cleanup_ensure status=created schedule_id={schedule_id}",
        schedule_id=spec.schedule_id,
    )
