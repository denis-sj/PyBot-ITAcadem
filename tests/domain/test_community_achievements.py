from datetime import date, timedelta

from pybot.domain.services.community_achievements import AchievementCode, calculate_attendance_achievements


def test_weeks_run_from_monday_through_sunday() -> None:
    calculation = calculate_attendance_achievements(
        [date(2026, 9, 28), date(2026, 10, 4), date(2026, 10, 5)],
        today=date(2026, 10, 6),
    )

    assert calculation.current_streak == 2
    assert calculation.earned_on.get(AchievementCode.STREAK_4) is None


def test_unfinished_current_week_does_not_break_the_streak() -> None:
    calculation = calculate_attendance_achievements(
        [date(2026, 9, 21), date(2026, 9, 28)],
        today=date(2026, 10, 6),
    )

    assert calculation.current_streak == 2


def test_a_fully_missed_week_breaks_the_current_streak() -> None:
    calculation = calculate_attendance_achievements(
        [date(2026, 9, 14), date(2026, 9, 21)],
        today=date(2026, 10, 6),
    )

    assert calculation.current_streak == 0


def test_historical_streak_achievement_is_kept_after_a_later_gap() -> None:
    visit_dates = [date(2026, 8, 3) + timedelta(days=7 * week) for week in range(4)]
    calculation = calculate_attendance_achievements(visit_dates, today=date(2026, 10, 6))

    assert calculation.current_streak == 0
    assert calculation.earned_on[AchievementCode.STREAK_4] == date(2026, 8, 24)


def test_visit_count_achievements_record_the_threshold_visit_date() -> None:
    visit_dates = [date(2026, 1, 1) + timedelta(days=day) for day in range(50)]
    calculation = calculate_attendance_achievements(visit_dates, today=date(2026, 3, 1))

    assert calculation.earned_on[AchievementCode.FIRST_STEP] == date(2026, 1, 1)
    assert calculation.earned_on[AchievementCode.VISITS_10] == date(2026, 1, 10)
    assert calculation.earned_on[AchievementCode.VISITS_25] == date(2026, 1, 25)
    assert calculation.earned_on[AchievementCode.VISITS_50] == date(2026, 2, 19)


def test_comeback_requires_twenty_eight_full_days_without_a_visit() -> None:
    calculation = calculate_attendance_achievements(
        [date(2026, 1, 1), date(2026, 1, 29)],
        today=date(2026, 1, 30),
    )

    assert calculation.earned_on[AchievementCode.COMEBACK] == date(2026, 1, 29)
