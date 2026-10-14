"""Pure calendar and input rules for weekly community check-ins."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import StrEnum

from ...core.config import YEKATERINBURG_TZ

_WEEK_DAYS = 7
_PROMPT_DAY_OFFSET = 2
_PROMPT_HOUR = 15
_REPORT_DELAY_WEEKS = 1
_MAX_GOAL_LENGTH = 200


class CheckinStatus(StrEnum):
    """Normalized outcome of a participant's weekly check-in."""

    IN_PROGRESS = "in_progress"
    STUCK = "stuck"
    NO_MOTIVATION = "no_motivation"
    GOAL_ACHIEVED = "goal_achieved"
    NEWBIE = "newbie"
    NO_SUBSTANTIVE_ANSWER = "no_substantive_answer"


class GoalStatus(StrEnum):
    """Lifecycle states for participant goals."""

    ACTIVE = "active"
    ACHIEVED = "achieved"
    CANCELLED = "cancelled"


class NotificationPreferenceKey(StrEnum):
    """Persisted opt-out switches shown in the participant notification menu."""

    CHECKINS = "checkins_enabled"
    MONTHLY_SUMMARY = "monthly_summary_enabled"
    ACHIEVEMENTS = "achievement_notifications_enabled"
    STREAK_WARNINGS = "streak_warnings_enabled"
    MEETING_INVITATIONS = "meeting_invitations_enabled"


def get_current_checkin_week_start(now: datetime) -> date:
    """Return Monday for the current Yekaterinburg calendar week."""
    local_now = now.astimezone(YEKATERINBURG_TZ)
    return local_now.date() - timedelta(days=local_now.weekday())


def get_checkin_prompt_at(week_start: date) -> datetime:
    """Return the Wednesday 15:00 local timestamp for a Monday campaign week."""
    return datetime.combine(week_start + timedelta(days=_PROMPT_DAY_OFFSET), time(_PROMPT_HOUR), YEKATERINBURG_TZ)


def get_checkin_deadline(week_start: date) -> datetime:
    """Return the following Wednesday at 15:00, when campaign answers close."""
    return get_checkin_prompt_at(week_start) + timedelta(days=_WEEK_DAYS)


def get_reported_checkin_week_start(report_date: date) -> date:
    """Return the previous Monday campaign for the Thursday report date."""
    current_week_start = report_date - timedelta(days=report_date.weekday())
    return current_week_start - timedelta(days=_WEEK_DAYS * _REPORT_DELAY_WEEKS)


def is_checkin_response_open(week_start: date, now: datetime) -> bool:
    """Check whether the campaign has started and its response window is still open."""
    local_now = now.astimezone(YEKATERINBURG_TZ)
    return get_checkin_prompt_at(week_start) <= local_now < get_checkin_deadline(week_start)


def normalize_goal_text(value: str) -> str:
    """Trim a goal, collapse whitespace and enforce the 200-character limit."""
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Цель не должна быть пустой")
    if len(normalized) > _MAX_GOAL_LENGTH:
        raise ValueError(f"Цель должна содержать не больше {_MAX_GOAL_LENGTH} символов")
    return normalized
