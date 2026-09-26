from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from pybot.presentation.bot.tg_bot_run import cleanup_stale_attendance_drafts_on_startup
from pybot.services.attendance_service import AttendanceService


class FakeContainer:
    def __init__(self, attendance_service: Any) -> None:
        self.attendance_service = attendance_service
        self.request_container = AsyncMock()
        self.request_container.get = AsyncMock(return_value=attendance_service)

    @asynccontextmanager
    async def __call__(self):
        yield self.request_container


@pytest.mark.asyncio
async def test_cleanup_stale_attendance_drafts_on_startup_success() -> None:
    service = AsyncMock(spec=AttendanceService)
    service.cleanup_stale_drafts = AsyncMock(return_value=5)
    container = FakeContainer(attendance_service=service)

    cleaned = await cleanup_stale_attendance_drafts_on_startup(cast(Any, container))

    assert cleaned == 5
    service.cleanup_stale_drafts.assert_awaited_once()
    container.request_container.get.assert_awaited_once_with(AttendanceService)


@pytest.mark.asyncio
async def test_cleanup_stale_attendance_drafts_on_startup_db_error_swallowed() -> None:
    service = AsyncMock(spec=AttendanceService)
    service.cleanup_stale_drafts = AsyncMock(side_effect=RuntimeError("Database failure"))
    container = FakeContainer(attendance_service=service)

    cleaned = await cleanup_stale_attendance_drafts_on_startup(cast(Any, container))

    assert cleaned == 0
    service.cleanup_stale_drafts.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_stale_attendance_drafts_on_startup_resolution_error_swallowed() -> None:
    from unittest.mock import Mock

    container = Mock(side_effect=RuntimeError("Container resolution failure"))

    cleaned = await cleanup_stale_attendance_drafts_on_startup(cast(Any, container))

    assert cleaned == 0
