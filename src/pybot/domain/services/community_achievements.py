"""Pure attendance and achievement rules for community activity."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise

_DAYS_PER_WEEK = 7
_COMEBACK_GAP_DAYS = 28


class AchievementCode(StrEnum):
    """Stable achievement identifiers persisted in the database."""

    FIRST_STEP = "first_step"
    STREAK_4 = "streak_4"
    STREAK_8 = "streak_8"
    STREAK_16 = "streak_16"
    VISITS_10 = "visits_10"
    VISITS_25 = "visits_25"
    VISITS_50 = "visits_50"
    COMEBACK = "comeback"
    TOP_WEEK = "top_week"
    GOAL_DONE = "goal_done"
    MENTOR = "mentor"


@dataclass(frozen=True, slots=True)
class AttendanceAchievementCalculation:
    """Calculated attendance milestones and the current weekly streak."""

    current_streak: int
    earned_on: dict[AchievementCode, date]


def calculate_attendance_achievements(
    attendance_dates: Sequence[date],
    *,
    today: date,
) -> AttendanceAchievementCalculation:
    """Calculate historical visit achievements and the current streak.

    A week starts on Monday and ends on Sunday. The unfinished current week does
    not break a streak, while any earlier empty week does. Historical streak
    achievements are retained in the result even when a later gap breaks the
    current streak.
    """
    visit_dates = sorted(set(attendance_dates))
    if not visit_dates:
        return AttendanceAchievementCalculation(current_streak=0, earned_on={})

    earned_on: dict[AchievementCode, date] = {AchievementCode.FIRST_STEP: visit_dates[0]}
    earned_on.update(_calculate_visit_count_achievements(visit_dates))
    comeback_date = _find_comeback_date(visit_dates)
    if comeback_date is not None:
        earned_on[AchievementCode.COMEBACK] = comeback_date

    dates_by_week = _group_visit_dates_by_week(visit_dates)
    earned_on.update(_calculate_streak_achievements(dates_by_week))
    current_streak = _calculate_current_streak(dates_by_week, today)
    return AttendanceAchievementCalculation(current_streak=current_streak, earned_on=earned_on)


def _calculate_visit_count_achievements(visit_dates: Sequence[date]) -> dict[AchievementCode, date]:
    """Return visit-count milestones with the date of the threshold visit."""
    earned_on: dict[AchievementCode, date] = {}
    for threshold, code in (
        (10, AchievementCode.VISITS_10),
        (25, AchievementCode.VISITS_25),
        (50, AchievementCode.VISITS_50),
    ):
        if len(visit_dates) >= threshold:
            earned_on[code] = visit_dates[threshold - 1]
    return earned_on


def _find_comeback_date(visit_dates: Sequence[date]) -> date | None:
    """Find the first visit after a gap of at least four full weeks."""
    return next(
        (
            visit
            for previous_visit, visit in pairwise(visit_dates)
            if (visit - previous_visit).days >= _COMEBACK_GAP_DAYS
        ),
        None,
    )


def _group_visit_dates_by_week(visit_dates: Sequence[date]) -> dict[date, list[date]]:
    """Group dates by their Monday start date."""
    dates_by_week: dict[date, list[date]] = defaultdict(list)
    for visit_date in visit_dates:
        monday = visit_date - timedelta(days=visit_date.weekday())
        dates_by_week[monday].append(visit_date)
    return dates_by_week


def _calculate_streak_achievements(dates_by_week: dict[date, list[date]]) -> dict[AchievementCode, date]:
    """Return the earliest earned date for each historical weekly streak badge."""
    earned_on: dict[AchievementCode, date] = {}
    week_starts = sorted(dates_by_week)
    thresholds = (
        (4, AchievementCode.STREAK_4),
        (8, AchievementCode.STREAK_8),
        (16, AchievementCode.STREAK_16),
    )
    run_start_index = 0
    for week_index in range(1, len(week_starts) + 1):
        is_end_of_run = _is_end_of_week_run(week_starts, week_index)
        if not is_end_of_run:
            continue

        run_length = week_index - run_start_index
        for threshold, code in thresholds:
            if run_length >= threshold:
                threshold_week = week_starts[run_start_index + threshold - 1]
                milestone_date = dates_by_week[threshold_week][0]
                current_earned_date = earned_on.get(code)
                if current_earned_date is None or milestone_date < current_earned_date:
                    earned_on[code] = milestone_date
        run_start_index = week_index
    return earned_on


def _is_end_of_week_run(week_starts: Sequence[date], week_index: int) -> bool:
    """Return whether a run ends at the current sorted-week index."""
    return (
        week_index == len(week_starts) or (week_starts[week_index] - week_starts[week_index - 1]).days > _DAYS_PER_WEEK
    )


def _calculate_current_streak(dates_by_week: dict[date, list[date]], today: date) -> int:
    """Count the current run, allowing the in-progress week to have no visit yet."""
    current_week = today - timedelta(days=today.weekday())
    last_completed_week = current_week - timedelta(days=_DAYS_PER_WEEK)
    last_streak_week = current_week if current_week in dates_by_week else last_completed_week
    current_streak = 0
    if last_streak_week in dates_by_week:
        week_start = last_streak_week
        while week_start in dates_by_week:
            current_streak += 1
            week_start -= timedelta(days=_DAYS_PER_WEEK)
    return current_streak
