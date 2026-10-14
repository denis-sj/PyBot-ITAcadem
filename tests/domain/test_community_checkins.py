from datetime import date, datetime, timedelta

import pytest

from pybot.core.config import YEKATERINBURG_TZ
from pybot.domain.services.community_checkins import (
    get_checkin_deadline,
    get_checkin_prompt_at,
    get_current_checkin_week_start,
    get_reported_checkin_week_start,
    is_checkin_response_open,
    normalize_goal_text,
)


def test_checkin_campaign_uses_yekaterinburg_calendar_week() -> None:
    now = datetime(2026, 10, 14, 14, 59, tzinfo=YEKATERINBURG_TZ)

    assert get_current_checkin_week_start(now) == date(2026, 10, 12)
    assert get_checkin_prompt_at(date(2026, 10, 12)) == datetime(2026, 10, 14, 15, tzinfo=YEKATERINBURG_TZ)
    assert get_checkin_deadline(date(2026, 10, 12)) == datetime(2026, 10, 21, 15, tzinfo=YEKATERINBURG_TZ)


def test_response_window_includes_prompt_time_but_closes_next_wednesday_at_1500() -> None:
    week_start = date(2026, 10, 12)
    prompt_at = get_checkin_prompt_at(week_start)
    deadline = get_checkin_deadline(week_start)

    assert not is_checkin_response_open(week_start, prompt_at - timedelta(microseconds=1))
    assert is_checkin_response_open(week_start, prompt_at)
    assert is_checkin_response_open(week_start, deadline - timedelta(microseconds=1))
    assert not is_checkin_response_open(week_start, deadline)


def test_thursday_report_covers_the_previous_checkin_campaign() -> None:
    assert get_reported_checkin_week_start(date(2026, 10, 22)) == date(2026, 10, 12)


def test_goal_text_is_trimmed_and_limited_to_200_characters() -> None:
    assert normalize_goal_text("  устроиться   на стажировку  ") == "устроиться на стажировку"
    assert normalize_goal_text("я" * 200) == "я" * 200

    with pytest.raises(ValueError, match="200"):
        normalize_goal_text("я" * 201)
    with pytest.raises(ValueError, match="пуст"):
        normalize_goal_text("   ")
