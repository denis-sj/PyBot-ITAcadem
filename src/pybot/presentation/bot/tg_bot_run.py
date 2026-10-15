"""Модуль бота IT Academ."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_dialog import setup_dialogs
from dishka import AsyncContainer
from dishka.integrations.aiogram import setup_dishka

from ...core import logger
from ...core.config import AppSettings, get_settings
from ...di.containers import setup_container
from ...services import AttendanceService, SystemRuntimeAlertsService
from ...services.ports.agent import AgentMemoryPort
from .dialogs import user_router
from .handlers import (
    attendance_router,
    broadcast_router,
    common_router,
    community_checkin_router,
    community_meetings_router,
    notification_preferences_router,
    points_router,
    profile_router,
    roles_router,
)
from .handlers.agent import create_agent_router
from .handlers.common.dialog_errors import register_dialog_error_handlers
from .handlers.registration_stack_exit import registration_stack_exit_router
from .middlewares import (
    LoggerMiddleware,
    RateLimitMiddleware,
    RoleMiddleware,
    UserActivityMiddleware,
)


async def setup_dispatcher(settings: AppSettings) -> Dispatcher:
    """Create dispatcher with the configured FSM backend."""
    if settings.fsm_storage_backend == "redis":
        storage = RedisStorage.from_url(
            settings.redis_url,
            key_builder=DefaultKeyBuilder(with_destiny=True),
        )
        logger.info("event=fsm_setup backend=redis redis_url={redis_url}", redis_url=settings.redis_url)
        return Dispatcher(storage=storage)

    logger.info("event=fsm_setup backend=memory")
    return Dispatcher()


async def setup_middlewares(dp: Dispatcher, settings: AppSettings) -> None:
    """Attach configured middleware stack to the dispatcher."""
    if settings.enable_logging_middleware:
        logging_middleware = LoggerMiddleware(settings, enabled=True)
        dp.message.middleware(logging_middleware)
        dp.callback_query.middleware(logging_middleware)
        dp.inline_query.middleware(logging_middleware)
        logger.info("event=middleware_setup middleware=LoggerMiddleware status=enabled")
    else:
        logger.info("event=middleware_setup middleware=LoggerMiddleware status=disabled")

    if settings.enable_user_activity_middleware:
        dp.message.middleware(UserActivityMiddleware())
        dp.callback_query.middleware(UserActivityMiddleware())
        logger.info("event=middleware_setup middleware=UserActivityMiddleware status=enabled")
    else:
        logger.info("event=middleware_setup middleware=UserActivityMiddleware status=disabled")

    if settings.enable_role_middleware:
        dp.message.middleware(RoleMiddleware())
        dp.callback_query.middleware(RoleMiddleware())
        dp.inline_query.middleware(RoleMiddleware())
        logger.info("event=middleware_setup middleware=RoleMiddleware status=enabled")
    else:
        logger.info("event=middleware_setup middleware=RoleMiddleware status=disabled")

    if settings.enable_rate_limit:
        dp.update.middleware(RateLimitMiddleware(settings=settings))
        dp.message.middleware(RateLimitMiddleware(settings=settings))
        dp.callback_query.middleware(RateLimitMiddleware(settings=settings))
        logger.info("event=middleware_setup middleware=RateLimitMiddleware status=enabled")
    else:
        logger.info("event=middleware_setup middleware=RateLimitMiddleware status=disabled")


async def setup_di(dp: Dispatcher) -> AsyncContainer:
    """Initialize the DI container and connect it to aiogram."""
    container = await setup_container()
    logger.debug("event=di_setup status=success container={container}", container=container)
    setup_dishka(container, dp, auto_inject=True)
    return container


async def setup_bot(container: AsyncContainer) -> Bot:
    """Resolve the bot instance from the DI container."""
    return await container.get(Bot)


async def setup_runtime_alerts_service(container: AsyncContainer) -> SystemRuntimeAlertsService:
    """Resolve runtime alerts service from the app container."""
    return await container.get(SystemRuntimeAlertsService)


def setup_handlers(dp: Dispatcher) -> None:
    """Attach routers and dialog layer."""
    register_dialog_error_handlers(dp)
    dp.include_router(registration_stack_exit_router)
    dp.include_router(common_router)
    dp.include_router(points_router)
    dp.include_router(profile_router)
    dp.include_router(user_router)
    dp.include_router(roles_router)
    dp.include_router(broadcast_router)
    dp.include_router(attendance_router)
    setup_dialogs(dp)
    dp.include_router(community_checkin_router)
    dp.include_router(notification_preferences_router)
    dp.include_router(community_meetings_router)


async def setup_agent_handler(dp: Dispatcher, container: AsyncContainer, settings: AppSettings) -> None:
    """Register agent last, only when explicitly enabled."""
    topic_configured = settings.agent_group_chat_id is not None and settings.agent_topic_id is not None
    logger.info(
        "event=agent_setup status={status} model={model} topic_configured={topic_configured}",
        status="enabled" if settings.agent_enabled else "disabled",
        model=settings.agent_model,
        topic_configured=str(topic_configured).lower(),
    )
    if settings.agent_enabled:
        memory = await container.get(AgentMemoryPort)
        dp.include_router(create_agent_router(settings, memory))


async def notify_startup_alert(runtime_alerts_service: SystemRuntimeAlertsService) -> None:
    """Send startup alert without breaking bot bootstrap on delivery failure."""
    try:
        await runtime_alerts_service.notify_startup()
        logger.info("event=runtime_alert phase=startup status=completed")
    except Exception:
        logger.exception("event=runtime_alert phase=startup status=failed")


async def cleanup_stale_attendance_drafts_on_startup(container: AsyncContainer) -> int:
    """Clean up expired attendance drafts on bot startup."""
    try:
        async with container() as request_container:
            attendance_service = await request_container.get(AttendanceService)
            count = await attendance_service.cleanup_stale_drafts()
            logger.info(
                "event=attendance_stale_drafts_cleanup phase=startup count={count}",
                count=count,
            )
            return count
    except Exception:
        logger.exception("event=attendance_stale_drafts_cleanup phase=startup status=failed")
        return 0


async def notify_shutdown_alert(runtime_alerts_service: SystemRuntimeAlertsService) -> None:
    """Send shutdown alert without breaking graceful shutdown on delivery failure."""
    try:
        await runtime_alerts_service.notify_shutdown()
        logger.info("event=runtime_alert phase=shutdown status=completed")
    except Exception:
        logger.exception("event=runtime_alert phase=shutdown status=failed")


async def wait_for_startup_alert(task: asyncio.Task[None] | None, timeout_s: float = 5.0) -> None:
    """Wait for startup alert task completion without blocking shutdown forever."""
    if task is None:
        return

    try:
        await asyncio.wait_for(task, timeout=timeout_s)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.warning("event=runtime_alert phase=startup status=timeout_cancelled")


async def tg_bot_main() -> None:
    """Run the bot with a graceful shutdown path."""
    container: AsyncContainer | None = None
    dp: Dispatcher | None = None
    runtime_alerts_service: SystemRuntimeAlertsService | None = None
    startup_alert_task: asyncio.Task[None] | None = None
    try:
        logger.info("event=bot_runtime_init status=started")
        settings = get_settings()
        dp = await setup_dispatcher(settings)
        container = await setup_di(dp)
        bot = await setup_bot(container)
        runtime_alerts_service = await setup_runtime_alerts_service(container)
        await setup_middlewares(dp, settings)
        setup_handlers(dp)
        await setup_agent_handler(dp, container, settings)
        await bot.delete_webhook(drop_pending_updates=settings.bot_mode != "prod")
        await cleanup_stale_attendance_drafts_on_startup(container)
        logger.info("event=bot_runtime_init status=completed")
        logger.info("event=polling status=started")
        startup_alert_task = asyncio.create_task(notify_startup_alert(runtime_alerts_service))
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("event=bot_runtime_shutdown reason=cancelled")
        raise
    except Exception:
        logger.exception("event=runtime_unexpected_error")
        raise
    finally:
        logger.info("event=graceful_shutdown status=started")

        await wait_for_startup_alert(startup_alert_task)

        if runtime_alerts_service is not None:
            await notify_shutdown_alert(runtime_alerts_service)

        if dp is not None:
            storage: BaseStorage | None = getattr(dp, "storage", None)
            if storage is not None:
                try:
                    await storage.close()
                    logger.info("event=fsm_storage_close status=success")
                except Exception:
                    logger.exception("event=fsm_storage_close status=failed")

        if container is not None:
            try:
                await container.close()
                logger.info("event=container_close status=success")
            except Exception:
                logger.exception("event=container_close status=failed")

        logger.info("event=graceful_shutdown status=completed")
