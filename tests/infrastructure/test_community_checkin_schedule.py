from pybot.infrastructure.taskiq.taskiq_community_checkin_schedule import (
    COMMUNITY_CHECKIN_SCHEDULES,
    COMMUNITY_CHECKIN_TIMEZONE,
)


def test_community_jobs_have_the_specified_yekaterinburg_schedule() -> None:
    assert COMMUNITY_CHECKIN_TIMEZONE == "Asia/Yekaterinburg"
    assert COMMUNITY_CHECKIN_SCHEDULES == (
        ("community_checkins:weekly_prompt", "community_checkins.send_weekly_prompts", "0 15 * * 3"),
        ("community_checkins:weekly_reminder", "community_checkins.send_weekly_reminders", "0 11 * * 4"),
        ("community_checkins:pulse_report", "community_checkins.deliver_pulse_report", "0 15 * * 4"),
        ("community_checkins:streak_warning", "community_checkins.warn_at_risk_streaks", "0 12 * * 6"),
    )
