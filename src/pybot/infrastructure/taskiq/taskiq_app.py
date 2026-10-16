from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dishka import AsyncContainer
from dishka.integrations.taskiq import setup_dishka
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask, TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.kicker import AsyncKicker
from taskiq.middlewares.smart_retry_middleware import SmartRetryMiddleware
from taskiq_redis import ListRedisScheduleSource, RedisStreamBroker

from ...core import logger
from ...core.config import AppSettings, get_settings
from ...di.containers import setup_taskiq_container
from ...dto import NotificationTaskPayload
from ...services.ports import NotificationTemporaryError
from .taskiq_attendance_schedule import (
    ATTENDANCE_CLEANUP_SCHEDULE_ID,
    ATTENDANCE_CLEANUP_TASK_NAME,
    AttendanceCleanupScheduleSpec,
)
from .taskiq_attendance_schedule import (
    ensure_attendance_cleanup_schedule as ensure_attendance_cleanup_schedule_in_source,
)
from .taskiq_community_achievement_schedule import (
    ensure_community_achievement_schedules as ensure_community_achievement_schedules_in_source,
)
from .taskiq_community_checkin_schedule import (
    ensure_community_checkin_schedules as ensure_community_checkin_schedules_in_source,
)
from .taskiq_community_meeting_schedule import ensure_community_meeting_schedule
from .taskiq_meme_schedule import ensure_weekly_meme_schedule as ensure_weekly_meme_schedule_in_source
from .taskiq_weekly_leaderboard_schedule import (
    LEADERBOARD_WEEKLY_SCHEDULE_ID,
    LEADERBOARD_WEEKLY_TASK_NAME,
    WeeklyLeaderboardScheduleSpec,
)
from .taskiq_weekly_leaderboard_schedule import (
    ensure_weekly_leaderboard_schedule as ensure_weekly_leaderboard_schedule_in_source,
)
from .taskiq_weekly_leaderboard_wiring import register_weekly_leaderboard_wiring
from .tasks import TaskRegistry, register_all_tasks
from .tasks.community_achievements import (
    PUBLISH_ACHIEVEMENT_DIGEST_TASK_NAME,
    PUBLISH_TOP_WEEK_TASK_NAME,
    RECALCULATE_ACHIEVEMENTS_TASK_NAME,
)
from .tasks.community_checkins import (
    CHECKIN_PROMPTS_TASK_NAME,
    CHECKIN_REMINDERS_TASK_NAME,
    PULSE_REPORT_TASK_NAME,
    STREAK_WARNINGS_TASK_NAME,
)


@dataclass(slots=True)
class _TaskiqRuntimeState:
    """Stores process-local TaskIQ runtime objects."""

    broker: AsyncBroker | None = None
    scheduler: TaskiqScheduler | None = None
    schedule_source: ListRedisScheduleSource | None = None
    container: AsyncContainer | None = None
    task_registry: TaskRegistry | None = None


_runtime_state = _TaskiqRuntimeState()


async def _on_worker_startup(_state: TaskiqState) -> None:
    """Initialize Dishka container for TaskIQ worker process."""
    if _runtime_state.container is not None:
        logger.warning("событие=инициализация_taskiq_worker status=skipped причина=container_already_initialized")
        return
    if _runtime_state.broker is None:
        raise RuntimeError("TaskIQ broker is not initialized.")

    container = setup_taskiq_container()
    setup_dishka(container=container, broker=_runtime_state.broker)
    _runtime_state.container = container
    logger.info("событие=инициализация_taskiq_worker status=success")


async def _on_worker_shutdown(_state: TaskiqState) -> None:
    """Close Dishka container on worker shutdown."""
    if _runtime_state.container is None:
        logger.warning("событие=завершение_taskiq_worker status=skipped причина=container_not_initialized")
        return

    await _runtime_state.container.close()
    _runtime_state.container = None
    logger.info("событие=завершение_taskiq_worker status=success")


def get_taskiq_task_registry(settings: AppSettings | None = None) -> TaskRegistry:
    """Возвращает реестр зарегистрированных задач TaskIQ.

    Если брокер еще не инициализирован, выполняет его инициализацию.

    Args:
        settings: Настройки бота. Если не указаны, используются настройки по умолчанию.

    Returns:
        TaskRegistry: Реестр задач.

    Raises:
        RuntimeError: Если задачи не были зарегистрированы.
    """
    get_taskiq_broker(settings)
    if _runtime_state.task_registry is None:
        raise RuntimeError("TaskIQ tasks are not registered.")
    return _runtime_state.task_registry


def get_taskiq_notification_task(
    settings: AppSettings | None = None,
) -> AsyncTaskiqDecoratedTask[..., NotificationTaskPayload]:
    """Возвращает декорированный объект задачи для отправки уведомлений.

    Args:
        settings: Настройки бота.

    Returns:
        AsyncTaskiqDecoratedTask: Объект задачи TaskIQ.
    """
    return get_taskiq_task_registry(settings).notification_task


def get_taskiq_weekly_leaderboard_task(
    settings: AppSettings | None = None,
) -> AsyncTaskiqDecoratedTask[..., dict[str, int]]:
    """Возвращает декорированный объект задачи для публикации еженедельного лидерборда.

    Args:
        settings: Настройки бота.

    Returns:
        AsyncTaskiqDecoratedTask: Объект задачи TaskIQ.
    """
    return get_taskiq_task_registry(settings).weekly_leaderboard_task


def get_taskiq_attendance_cleanup_task(
    settings: AppSettings | None = None,
) -> AsyncTaskiqDecoratedTask[..., int]:
    """Возвращает декорированный объект задачи для очистки просроченных черновиков посещаемости.

    Args:
        settings: Настройки бота.

    Returns:
        AsyncTaskiqDecoratedTask: Объект задачи TaskIQ.
    """
    return get_taskiq_task_registry(settings).attendance_cleanup_task


def get_taskiq_achievement_recalculation_task(
    settings: AppSettings | None = None,
) -> AsyncTaskiqDecoratedTask[..., Any]:
    """Return the registered post-commit award recalculation task."""
    return get_taskiq_task_registry(settings).achievement_recalculation_task


def _resolve_attendance_cleanup_kicker() -> AsyncKicker[Any, Any]:
    return get_taskiq_attendance_cleanup_task().kicker()


async def ensure_attendance_cleanup_schedule(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Идемпотентно гарантирует наличие расписания очистки просроченных черновиков.

    Проверяет, является ли текущий процесс планировщиком и включена ли функция в настройках.

    Args:
        broker: Брокер TaskIQ.
        schedule_source: Источник расписаний Redis.
        settings: Настройки бота.
    """
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        logger.debug("событие=attendance_cleanup_ensure status=skipped причина=not_scheduler_process")
        return

    if not runtime_settings.attendance_stale_draft_cleanup_enabled:
        logger.info("событие=attendance_cleanup_ensure status=skipped причина=disabled")
        return

    source = schedule_source or get_taskiq_schedule_source(runtime_settings)
    await ensure_attendance_cleanup_schedule_in_source(
        source=source,
        spec=AttendanceCleanupScheduleSpec(
            cron_expression=str(runtime_settings.attendance_stale_draft_cleanup_cron),
            schedule_id=ATTENDANCE_CLEANUP_SCHEDULE_ID,
            task_name=ATTENDANCE_CLEANUP_TASK_NAME,
        ),
        resolve_kicker=_resolve_attendance_cleanup_kicker,
    )


async def _on_client_startup_attendance(_state: TaskiqState) -> None:
    """Обработчик события запуска клиента TaskIQ для настройки очистки черновиков."""
    await ensure_attendance_cleanup_schedule()


def _resolve_achievement_kicker(task_name: str) -> AsyncKicker[Any, Any]:
    task_registry = get_taskiq_task_registry()
    if task_name == PUBLISH_ACHIEVEMENT_DIGEST_TASK_NAME:
        return task_registry.achievement_digest_task.kicker()
    if task_name == PUBLISH_TOP_WEEK_TASK_NAME:
        return task_registry.achievement_top_week_task.kicker()
    if task_name == RECALCULATE_ACHIEVEMENTS_TASK_NAME:
        return task_registry.achievement_recalculation_task.kicker()
    raise ValueError(f"Unknown community achievement task: {task_name}")


async def ensure_community_achievement_schedules(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Ensure configured daily digest and weekly top schedules in the scheduler process."""
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        return

    await ensure_community_achievement_schedules_in_source(
        source=schedule_source or get_taskiq_schedule_source(runtime_settings),
        settings=runtime_settings,
        resolve_kicker=_resolve_achievement_kicker,
    )


async def _on_client_startup_community_achievements(_state: TaskiqState) -> None:
    """Configure community achievement schedules when TaskIQ starts as a scheduler."""
    await ensure_community_achievement_schedules()


def _resolve_community_checkin_kicker(task_name: str) -> AsyncKicker[Any, Any]:
    """Return the registered kicker for one community check-in job."""
    tasks = get_taskiq_task_registry()
    if task_name == CHECKIN_PROMPTS_TASK_NAME:
        return tasks.checkin_prompts_task.kicker()
    if task_name == CHECKIN_REMINDERS_TASK_NAME:
        return tasks.checkin_reminders_task.kicker()
    if task_name == STREAK_WARNINGS_TASK_NAME:
        return tasks.streak_warnings_task.kicker()
    if task_name == PULSE_REPORT_TASK_NAME:
        return tasks.pulse_report_task.kicker()
    raise ValueError(f"Unknown community check-in task: {task_name}")


async def ensure_community_checkin_schedules(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Register the fixed Wednesday-to-Saturday engagement schedules."""
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        return
    await ensure_community_checkin_schedules_in_source(
        source=schedule_source or get_taskiq_schedule_source(runtime_settings),
        resolve_kicker=_resolve_community_checkin_kicker,
    )


async def _on_client_startup_community_checkins(_state: TaskiqState) -> None:
    """Configure weekly check-in schedules when the TaskIQ scheduler starts."""
    await ensure_community_checkin_schedules()


def _resolve_community_meeting_kicker() -> AsyncKicker[Any, Any]:
    return get_taskiq_task_registry().community_meeting_task.kicker()


async def ensure_community_meeting_schedules(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Register the recurring meeting reminder and finish-state task."""
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        return
    await ensure_community_meeting_schedule(
        source=schedule_source or get_taskiq_schedule_source(runtime_settings),
        resolve_kicker=_resolve_community_meeting_kicker,
    )


async def _on_client_startup_community_meetings(_state: TaskiqState) -> None:
    await ensure_community_meeting_schedules()


def _resolve_weekly_meme_kicker() -> AsyncKicker[Any, Any]:
    return get_taskiq_task_registry().weekly_meme_task.kicker()


async def ensure_weekly_meme_schedule(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Register the Wednesday 16:16 Yekaterinburg meme publication schedule."""
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        return
    await ensure_weekly_meme_schedule_in_source(
        source=schedule_source or get_taskiq_schedule_source(runtime_settings),
        settings=runtime_settings,
        resolve_kicker=_resolve_weekly_meme_kicker,
    )


async def _on_client_startup_weekly_meme(_state: TaskiqState) -> None:
    await ensure_weekly_meme_schedule()


def _resolve_publish_weekly_leaderboard_kicker() -> AsyncKicker[Any, Any]:
    return get_taskiq_weekly_leaderboard_task().kicker()


async def ensure_weekly_leaderboard_schedule(
    *,
    broker: AsyncBroker | None = None,
    schedule_source: ListRedisScheduleSource | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Идемпотентно гарантирует наличие расписания еженедельного лидерборда.

    Проверяет, является ли текущий процесс планировщиком и включена ли функция в настройках.

    Args:
        broker: Брокер TaskIQ.
        schedule_source: Источник расписаний Redis.
        settings: Настройки бота.

    Raises:
        RuntimeError: Если не установлен ID получателя при включенном расписании.
    """
    runtime_settings = settings or get_settings()
    runtime_broker = broker or get_taskiq_broker(runtime_settings)
    if not runtime_broker.is_scheduler_process:
        logger.debug("событие=leaderboard_weekly_ensure status=skipped причина=not_scheduler_process")
        return

    if not runtime_settings.leaderboard_weekly_enabled:
        logger.info("событие=leaderboard_weekly_ensure status=skipped причина=disabled")
        return

    recipient_id = runtime_settings.leaderboard_weekly_recipient_id
    if recipient_id is None:
        raise RuntimeError("LEADERBOARD_WEEKLY_RECIPIENT_ID must be set when LEADERBOARD_WEEKLY_ENABLED=true")

    source = schedule_source or get_taskiq_schedule_source(runtime_settings)
    await ensure_weekly_leaderboard_schedule_in_source(
        source=source,
        spec=WeeklyLeaderboardScheduleSpec(
            recipient_id=recipient_id,
            cron_expression=str(runtime_settings.leaderboard_weekly_cron),
            timezone_name=str(runtime_settings.leaderboard_weekly_timezone),
            limit=runtime_settings.leaderboard_weekly_limit,
            message_thread_id=runtime_settings.leaderboard_weekly_thread_id,
            schedule_id=LEADERBOARD_WEEKLY_SCHEDULE_ID,
            task_name=LEADERBOARD_WEEKLY_TASK_NAME,
        ),
        resolve_kicker=_resolve_publish_weekly_leaderboard_kicker,
    )


def get_taskiq_broker(settings: AppSettings | None = None) -> AsyncBroker:
    """Возвращает синглтон брокера TaskIQ.

    Args:
        settings: Настройки бота.

    Returns:
        AsyncBroker: Объект брокера TaskIQ.
    """
    if _runtime_state.broker is not None:
        return _runtime_state.broker

    runtime_settings = settings or get_settings()
    broker = RedisStreamBroker(runtime_settings.redis_url)
    _runtime_state.task_registry = register_all_tasks(
        broker=broker,
        settings=runtime_settings,
    )
    broker.add_event_handler(TaskiqEvents.WORKER_STARTUP, _on_worker_startup)
    broker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, _on_worker_shutdown)
    broker.add_event_handler(TaskiqEvents.CLIENT_STARTUP, _on_client_startup_attendance)
    broker.add_event_handler(TaskiqEvents.CLIENT_STARTUP, _on_client_startup_community_achievements)
    broker.add_event_handler(TaskiqEvents.CLIENT_STARTUP, _on_client_startup_community_checkins)
    broker.add_event_handler(TaskiqEvents.CLIENT_STARTUP, _on_client_startup_community_meetings)
    broker.add_event_handler(TaskiqEvents.CLIENT_STARTUP, _on_client_startup_weekly_meme)
    register_weekly_leaderboard_wiring(
        broker,
        ensure_weekly_leaderboard_schedule=ensure_weekly_leaderboard_schedule,
    )
    broker.add_middlewares(
        SmartRetryMiddleware(
            default_retry_count=runtime_settings.leaderboard_weekly_retry_max_retries,
            default_retry_label=False,
            default_delay=float(runtime_settings.leaderboard_weekly_retry_delay_s),
            use_jitter=runtime_settings.leaderboard_weekly_retry_use_jitter,
            use_delay_exponent=runtime_settings.leaderboard_weekly_retry_use_exponential_backoff,
            max_delay_exponent=float(runtime_settings.leaderboard_weekly_retry_max_delay_s),
            schedule_source=get_taskiq_schedule_source(runtime_settings),
            types_of_exceptions=(NotificationTemporaryError,),
        )
    )

    _runtime_state.broker = broker
    return _runtime_state.broker


def get_taskiq_schedule_source(settings: AppSettings | None = None) -> ListRedisScheduleSource:
    """Возвращает синглтон источника расписаний для планировщика.

    Args:
        settings: Настройки бота.

    Returns:
        ListRedisScheduleSource: Объект источника расписаний.
    """
    if _runtime_state.schedule_source is not None:
        return _runtime_state.schedule_source

    runtime_settings = settings or get_settings()
    _runtime_state.schedule_source = ListRedisScheduleSource(runtime_settings.redis_url)
    return _runtime_state.schedule_source


def get_taskiq_scheduler(settings: AppSettings | None = None) -> TaskiqScheduler:
    """Возвращает синглтон планировщика TaskIQ.

    Args:
        settings: Настройки бота.

    Returns:
        TaskiqScheduler: Объект планировщика.
    """
    if _runtime_state.scheduler is not None:
        return _runtime_state.scheduler

    runtime_settings = settings or get_settings()
    broker = get_taskiq_broker(runtime_settings)
    source = get_taskiq_schedule_source(runtime_settings)
    _runtime_state.scheduler = TaskiqScheduler(broker=broker, sources=[source])
    return _runtime_state.scheduler
