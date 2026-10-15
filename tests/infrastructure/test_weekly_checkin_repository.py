from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from pybot.dto.community_checkins import PulseReportRecipientDTO
from pybot.infrastructure.weekly_checkin_repository import WeeklyCheckinRepository


@pytest.mark.asyncio
async def test_consecutive_missed_weeks_stop_at_answered_or_missing_campaign() -> None:
    current_week = date(2026, 10, 5)
    result = Mock()
    result.all.return_value = [
        (1, current_week, None),
        (1, current_week - timedelta(days=7), None),
        (1, current_week - timedelta(days=14), datetime(2026, 9, 24)),
        (2, current_week, None),
        (2, current_week - timedelta(days=14), None),
    ]
    db = Mock()
    db.execute = AsyncMock(return_value=result)

    counts = await WeeklyCheckinRepository().find_consecutive_missed_weeks(
        db,
        user_ids=(1, 2, 3),
        current_week_start=current_week,
    )

    assert counts == {1: 2, 2: 1, 3: 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(("active_admin", "expected"), ((True, True), (False, False)))
async def test_report_recipient_is_checked_against_current_admin_and_block_status(active_admin, expected) -> None:
    query_result = Mock()
    query_result.scalar_one_or_none.return_value = 1 if active_admin else None
    db = Mock()
    db.execute = AsyncMock(return_value=query_result)

    available = await WeeklyCheckinRepository().is_available_report_recipient(
        db,
        PulseReportRecipientDTO(user_id=7, telegram_id=700),
    )

    executed_query = db.execute.await_args
    if executed_query is None:
        raise AssertionError("recipient eligibility query was not executed")
    query = executed_query.args[0]
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert available is expected
    assert "roles.name = 'Admin'" in compiled_query
    assert "users.bot_is_blocked IS false" in compiled_query
    assert "users.telegram_id = 700" in compiled_query
    assert "users.id = 7" in compiled_query
