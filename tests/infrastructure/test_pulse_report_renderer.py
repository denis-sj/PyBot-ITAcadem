from datetime import date

from pybot.domain.services.community_checkins import CheckinStatus
from pybot.dto.community_checkins import CheckinParticipantDTO, PulseReportDTO
from pybot.infrastructure.pulse_report_renderer import build_pulse_report_html


def test_renderer_escapes_user_content_and_renders_empty_non_responder_state() -> None:
    participant = CheckinParticipantDTO(
        user_id=1,
        first_name="<script>alert(1)</script>",
        last_name=None,
        telegram_username=None,
        goal_text="<b>Цель</b>",
        summary="<img src=x onerror=alert(1)>",
        status=CheckinStatus.IN_PROGRESS,
        visit_count=0,
        current_streak=0,
        last_visit=None,
        silent_weeks=0,
    )
    report = _report(
        invited_count=1,
        answered_count=1,
        answered_participants=(participant,),
    )

    html = build_pulse_report_html(report)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "Цели взяты" in html
    assert "Все участники ответили на чекин." in html


def test_renderer_does_not_claim_that_everyone_answered_when_no_one_was_invited() -> None:
    html = build_pulse_report_html(_report())

    assert "На эту неделю приглашений не было." in html
    assert "Все участники ответили на чекин." not in html


def _report(
    *,
    invited_count: int = 0,
    answered_count: int = 0,
    answered_participants: tuple[CheckinParticipantDTO, ...] = (),
) -> PulseReportDTO:
    return PulseReportDTO(
        week_start=date(2026, 10, 12),
        week_end=date(2026, 10, 18),
        invited_count=invited_count,
        answered_count=answered_count,
        needs_help_count=0,
        goals_achieved_count=0,
        visit_count=0,
        answered_delta=0,
        needs_help_delta=0,
        goals_achieved_delta=0,
        visits_delta=0,
        needs_help=(),
        answered_participants=answered_participants,
        achievements=(),
        non_responders=(),
        learning_technologies=(),
    )
