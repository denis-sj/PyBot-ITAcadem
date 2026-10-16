from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
import pytest
from taskiq import AsyncBroker, InMemoryBroker
from taskiq.scheduler.scheduled_task import ScheduledTask
from taskiq_redis import ListRedisScheduleSource

from pybot.core.config import AppSettings
from pybot.infrastructure.taskiq import taskiq_app
from pybot.infrastructure.taskiq.taskiq_attendance_schedule import (
    ATTENDANCE_CLEANUP_SCHEDULE_ID,
    ATTENDANCE_CLEANUP_TASK_NAME,
    AttendanceCleanupScheduleSpec,
    ensure_attendance_cleanup_schedule as ensure_attendance_cleanup_schedule_in_source,
    is_expected_attendance_cleanup_schedule,
)
from pybot.infrastructure.taskiq.tasks import TaskRegistry, register_all_tasks
from pybot.infrastructure.taskiq.tasks.attendance import (
    cleanup_stale_attendance_drafts_task,
    register_tasks as register_attendance_tasks,
)
from pybot.services.attendance_service import AttendanceService


class AppSettingsWithoutDotenv(AppSettings):
    """Subclass that disables loading from .env files for deterministic testing."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings)


class FakeScheduleSource:
    def __init__(self, schedules: list[SimpleNamespace] | None = None) -> None:
        self.get_schedules = AsyncMock(return_value=schedules or [])
        self.delete_schedule = AsyncMock()


class FakeKicker:
    def __init__(self) -> None:
        self.schedule_id: str | None = None
        self.calls: list[tuple[object, str, dict[str, Any]]] = []

    def with_schedule_id(self, schedule_id: str) -> FakeKicker:
        self.schedule_id = schedule_id
        return self

    async def schedule_by_cron(
        self,
        source: object,
        cron: str,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append((source, cron, kwargs))
        return SimpleNamespace(schedule_id=self.schedule_id)


def _attendance_settings(**overrides: Any) -> AppSettings:
    base = {
        "BOT_TOKEN": "123456:prod",
        "BOT_TOKEN_TEST": "123456:test",
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:5432/pybot_unit_test",
    }
    base.update(overrides)
    return cast(Any, AppSettingsWithoutDotenv)(**base)


# ---------------------------------------------------------------------------
# 1. Config & Settings
# ---------------------------------------------------------------------------


def test_attendance_cleanup_settings_defaults() -> None:
    settings = _attendance_settings()
    assert settings.attendance_stale_draft_cleanup_enabled is True
    assert str(settings.attendance_stale_draft_cleanup_cron) == "0 * * * *"


def test_attendance_cleanup_settings_custom_values() -> None:
    settings = _attendance_settings(
        ATTENDANCE_STALE_DRAFT_CLEANUP_ENABLED=False,
        ATTENDANCE_STALE_DRAFT_CLEANUP_CRON="15 * * * *",
    )
    assert settings.attendance_stale_draft_cleanup_enabled is False
    assert str(settings.attendance_stale_draft_cleanup_cron) == "15 * * * *"


def test_attendance_cleanup_settings_invalid_cron() -> None:
    with pytest.raises(ValidationError, match="ATTENDANCE_STALE_DRAFT_CLEANUP_CRON"):
        _attendance_settings(ATTENDANCE_STALE_DRAFT_CLEANUP_CRON="invalid-cron")


# ---------------------------------------------------------------------------
# 2. TaskIQ Task & Registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_stale_attendance_drafts_task_calls_service() -> None:
    service = AsyncMock(spec=AttendanceService)
    service.cleanup_stale_drafts = AsyncMock(return_value=7)

    result = await cleanup_stale_attendance_drafts_task(attendance_service=service)

    assert result == 7
    service.cleanup_stale_drafts.assert_awaited_once()


def test_register_attendance_tasks_creates_task() -> None:
    broker = InMemoryBroker()
    settings = _attendance_settings()

    task = register_attendance_tasks(broker=broker, settings=settings)

    assert task.task_name == ATTENDANCE_CLEANUP_TASK_NAME
    assert task.task_name in broker.get_all_tasks()


def test_register_all_tasks_includes_attendance_cleanup() -> None:
    broker = InMemoryBroker()
    settings = _attendance_settings()

    registry = register_all_tasks(broker=broker, settings=settings)

    assert isinstance(registry, TaskRegistry)
    assert registry.attendance_cleanup_task is not None
    assert registry.attendance_cleanup_task.task_name == ATTENDANCE_CLEANUP_TASK_NAME
    assert registry.weekly_meme_task.task_name == "weekly_meme.publish"


# ---------------------------------------------------------------------------
# 3. Schedule Spec & Matching
# ---------------------------------------------------------------------------


def test_is_expected_attendance_cleanup_schedule_matching() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    schedule = cast(
        ScheduledTask,
        SimpleNamespace(
            task_name=ATTENDANCE_CLEANUP_TASK_NAME,
            cron="0 * * * *",
        ),
    )
    assert is_expected_attendance_cleanup_schedule(schedule, spec=spec) is True


def test_is_expected_attendance_cleanup_schedule_mismatched_task_name() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    schedule = cast(
        ScheduledTask,
        SimpleNamespace(
            task_name="other.task",
            cron="0 * * * *",
        ),
    )
    assert is_expected_attendance_cleanup_schedule(schedule, spec=spec) is False


def test_is_expected_attendance_cleanup_schedule_mismatched_cron() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    schedule = cast(
        ScheduledTask,
        SimpleNamespace(
            task_name=ATTENDANCE_CLEANUP_TASK_NAME,
            cron="*/10 * * * *",
        ),
    )
    assert is_expected_attendance_cleanup_schedule(schedule, spec=spec) is False


# ---------------------------------------------------------------------------
# 4. ensure_attendance_cleanup_schedule in source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_in_source_raises_without_kicker() -> None:
    source = FakeScheduleSource()
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")

    with pytest.raises(RuntimeError, match="kicker resolver is not configured"):
        await ensure_attendance_cleanup_schedule_in_source(
            source=cast(ListRedisScheduleSource, source),
            spec=spec,
            resolve_kicker=None,
        )


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_in_source_noops_when_matching() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    existing = SimpleNamespace(
        schedule_id=ATTENDANCE_CLEANUP_SCHEDULE_ID,
        task_name=ATTENDANCE_CLEANUP_TASK_NAME,
        cron="0 * * * *",
    )
    source = FakeScheduleSource([existing])
    kicker = FakeKicker()

    await ensure_attendance_cleanup_schedule_in_source(
        source=cast(ListRedisScheduleSource, source),
        spec=spec,
        resolve_kicker=lambda: cast(Any, kicker),
    )

    source.get_schedules.assert_awaited_once()
    source.delete_schedule.assert_not_called()
    assert kicker.calls == []


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_in_source_replaces_stale() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    stale = SimpleNamespace(
        schedule_id=ATTENDANCE_CLEANUP_SCHEDULE_ID,
        task_name=ATTENDANCE_CLEANUP_TASK_NAME,
        cron="*/30 * * * *",
    )
    source = FakeScheduleSource([stale])
    kicker = FakeKicker()

    await ensure_attendance_cleanup_schedule_in_source(
        source=cast(ListRedisScheduleSource, source),
        spec=spec,
        resolve_kicker=lambda: cast(Any, kicker),
    )

    source.delete_schedule.assert_awaited_once_with(ATTENDANCE_CLEANUP_SCHEDULE_ID)
    assert kicker.schedule_id == ATTENDANCE_CLEANUP_SCHEDULE_ID
    assert len(kicker.calls) == 1
    src, cron, kwargs = kicker.calls[0]
    assert src is source
    assert cron == "0 * * * *"
    assert kwargs == {}


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_in_source_creates_when_missing() -> None:
    spec = AttendanceCleanupScheduleSpec(cron_expression="0 * * * *")
    source = FakeScheduleSource([])
    kicker = FakeKicker()

    await ensure_attendance_cleanup_schedule_in_source(
        source=cast(ListRedisScheduleSource, source),
        spec=spec,
        resolve_kicker=lambda: cast(Any, kicker),
    )

    source.delete_schedule.assert_not_called()
    assert kicker.schedule_id == ATTENDANCE_CLEANUP_SCHEDULE_ID
    assert len(kicker.calls) == 1
    src, cron, kwargs = kicker.calls[0]
    assert src is source
    assert cron == "0 * * * *"


# ---------------------------------------------------------------------------
# 5. ensure_attendance_cleanup_schedule in taskiq_app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_skips_outside_scheduler_process() -> None:
    settings = _attendance_settings()
    broker = SimpleNamespace(is_scheduler_process=False)
    source = FakeScheduleSource()

    await taskiq_app.ensure_attendance_cleanup_schedule(
        broker=cast(AsyncBroker, broker),
        schedule_source=cast(ListRedisScheduleSource, source),
        settings=settings,
    )

    source.get_schedules.assert_not_called()
    source.delete_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_skips_when_disabled() -> None:
    settings = _attendance_settings(ATTENDANCE_STALE_DRAFT_CLEANUP_ENABLED=False)
    broker = SimpleNamespace(is_scheduler_process=True)
    source = FakeScheduleSource()

    await taskiq_app.ensure_attendance_cleanup_schedule(
        broker=cast(AsyncBroker, broker),
        schedule_source=cast(ListRedisScheduleSource, source),
        settings=settings,
    )

    source.get_schedules.assert_not_called()
    source.delete_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_attendance_cleanup_schedule_runs_when_enabled_in_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _attendance_settings(
        ATTENDANCE_STALE_DRAFT_CLEANUP_ENABLED=True,
        ATTENDANCE_STALE_DRAFT_CLEANUP_CRON="0 * * * *",
    )
    broker = SimpleNamespace(is_scheduler_process=True)
    kicker = FakeKicker()
    source = FakeScheduleSource([])

    monkeypatch.setattr(taskiq_app, "_resolve_attendance_cleanup_kicker", lambda: kicker)

    await taskiq_app.ensure_attendance_cleanup_schedule(
        broker=cast(AsyncBroker, broker),
        schedule_source=cast(ListRedisScheduleSource, source),
        settings=settings,
    )

    source.get_schedules.assert_awaited_once()
    assert kicker.schedule_id == ATTENDANCE_CLEANUP_SCHEDULE_ID
    assert len(kicker.calls) == 1
    assert kicker.calls[0][1] == "0 * * * *"


@pytest.mark.asyncio
async def test_on_client_startup_attendance_calls_ensure(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ensure = AsyncMock()
    monkeypatch.setattr(taskiq_app, "ensure_attendance_cleanup_schedule", mock_ensure)

    await taskiq_app._on_client_startup_attendance(cast(Any, SimpleNamespace()))

    mock_ensure.assert_awaited_once()
