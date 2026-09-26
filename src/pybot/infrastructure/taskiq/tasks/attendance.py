from __future__ import annotations

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core import logger
from ....core.config import AppSettings
from ....services.attendance_service import AttendanceService

ATTENDANCE_CLEANUP_TASK_NAME = "attendance.cleanup_stale_drafts"


async def cleanup_stale_attendance_drafts_task(
    attendance_service: FromDishka[AttendanceService],
) -> int:
    """Очищает просроченные черновики посещаемости."""
    count = await attendance_service.cleanup_stale_drafts()
    logger.info("TaskIQ attendance stale drafts cleanup finished with count={count}", count=count)
    return count


def register_tasks(
    *,
    broker: AsyncBroker,
    settings: AppSettings,
) -> AsyncTaskiqDecoratedTask[..., Any]:
    """Регистрирует задачу очистки просроченных черновиков в брокере.

    Args:
        broker: Брокер TaskIQ.
        settings: Настройки приложения.

    Returns:
        AsyncTaskiqDecoratedTask: Декорированная задача.
    """
    _ = settings
    return broker.task(
        task_name=ATTENDANCE_CLEANUP_TASK_NAME,
    )(inject(patch_module=True)(cleanup_stale_attendance_drafts_task))
