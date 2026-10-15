from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

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
