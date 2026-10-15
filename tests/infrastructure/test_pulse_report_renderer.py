import re
from datetime import date

from pybot.domain.services.community_achievements import AchievementCode
from pybot.domain.services.community_checkins import CheckinStatus
from pybot.domain.services.technology_stack import TechnologyLevel
from pybot.dto.community_checkins import (
    CheckinParticipantDTO,
    PulseAchievementDTO,
    PulseReportDTO,
    PulseTechnologyDTO,
)
from pybot.dto.technology_stack import TechnologyStackItemDTO
from pybot.infrastructure.pulse_report_renderer import build_pulse_report_html


def test_renderer_escapes_user_content_and_renders_empty_non_responder_state() -> None:
    participant = _participant(
        1,
        first_name="<script>alert(1)</script>",
        goal_text="<b>Цель</b>",
        summary="<img src=x onerror=alert(1)>",
    )

    html = build_pulse_report_html(_report(invited_count=1, answered_participants=(participant,)))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "Цели взяты" in html
    assert "Все участники ответили на чекин." in html


def test_renderer_does_not_claim_that_everyone_answered_when_no_one_was_invited() -> None:
    html = build_pulse_report_html(_report())

    assert "На эту неделю приглашений не было." in html
    assert "Все участники ответили на чекин." not in html


def test_participant_is_rendered_once_in_priority_sections_and_remainder_count_matches() -> None:
    needs_help = _participant(1, status=CheckinStatus.STUCK)
    goal_achieved = _participant(2, status=CheckinStatus.GOAL_ACHIEVED)
    off_topic = _participant(3, status=CheckinStatus.NO_SUBSTANTIVE_ANSWER)
    in_progress = _participant(4, status=CheckinStatus.IN_PROGRESS)
    html = build_pulse_report_html(
        _report(
            invited_count=5,
            answered_count=4,
            needs_help=(needs_help, needs_help),
            answered_participants=(needs_help, goal_achieved, off_topic, in_progress, in_progress),
            non_responders=(_participant(4, status=None, summary=None),),
        )
    )

    for user_id in (1, 2, 3, 4):
        assert html.count(f'data-user-id="{user_id}"') == 1
    assert 'Кто чем занимается <span class="count">2 участника</span>' in html
    assert "Пометка: без ответа по существу." in html
    assert "Без ответа по существу" in html


def test_report_has_russian_period_gradient_header_and_seven_day_participant_metrics() -> None:
    participant = _participant(
        7,
        status=CheckinStatus.IN_PROGRESS,
        visit_count=3,
        current_streak=5,
        stack=(
            TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="Docker", level=TechnologyLevel.LEARNING),
        ),
    )
    html = build_pulse_report_html(_report(answered_count=1, answered_participants=(participant,)))

    assert "Чекин 14 октября · отчёт 15 октября 2026" in html
    assert "linear-gradient(135deg" in html
    assert ".hero-meta { min-width: 220px; text-align: right;" in html
    assert "Посещений за 7 дней" in html
    assert 'class="chip know">Python</span>' in html
    assert 'class="chip">Docker · изучаю</span>' in html
    assert "Посещений за 7 дней: <b>3</b>" in html
    assert "Текущая серия: <b>5 недель</b>" in html


def test_report_uses_svg_icon_for_every_achievement_code_and_contains_no_emoji() -> None:
    achievements = tuple(
        PulseAchievementDTO(code=code.value, title=code.value, participant_names=("Участник",))
        for code in AchievementCode
    )
    html = build_pulse_report_html(_report(achievements=achievements))

    for achievement in AchievementCode:
        assert html.count(f'data-code="{achievement.value}"') == 2
    assert _EMOJI_PATTERN.search(html) is None


def test_emoji_from_participant_content_is_removed_from_report_html() -> None:
    html = build_pulse_report_html(
        _report(answered_participants=(_participant(10, summary="Ура! 🔥 Отличный прогресс"),))
    )

    assert "Отличный прогресс" in html
    assert _EMOJI_PATTERN.search(html) is None


def test_non_responders_are_sorted_by_silent_weeks_and_four_weeks_are_highlighted() -> None:
    participants = (
        _participant(31, telegram_username="three_weeks", silent_weeks=3, last_visit=date(2026, 10, 1)),
        _participant(32, telegram_username="seven_weeks", silent_weeks=7, last_visit=date(2026, 9, 3)),
        _participant(33, telegram_username="four_weeks", silent_weeks=4, last_visit=date(2026, 9, 24)),
    )
    html = build_pulse_report_html(_report(invited_count=3, non_responders=participants))

    assert "<th>без ответа, нед.</th>" in html
    assert html.index("@seven_weeks") < html.index("@four_weeks") < html.index("@three_weeks")
    assert '<tr class="silent-critical" data-user-id="32"><td>@seven_weeks' in html
    assert '<tr class="silent-critical" data-user-id="33"><td>@four_weeks' in html
    assert '<tr class="" data-user-id="31"><td>@three_weeks' in html


def _participant(
    user_id: int,
    *,
    first_name: str = "Участник",
    telegram_username: str | None = None,
    goal_text: str | None = "Цель",
    summary: str | None = "Есть прогресс",
    status: CheckinStatus | None = CheckinStatus.IN_PROGRESS,
    visit_count: int = 0,
    current_streak: int = 0,
    last_visit: date | None = None,
    silent_weeks: int = 0,
    stack: tuple[TechnologyStackItemDTO, ...] = (),
) -> CheckinParticipantDTO:
    return CheckinParticipantDTO(
        user_id=user_id,
        first_name=first_name,
        last_name=None,
        telegram_username=telegram_username,
        goal_text=goal_text,
        summary=summary,
        status=status,
        visit_count=visit_count,
        current_streak=current_streak,
        last_visit=last_visit,
        silent_weeks=silent_weeks,
        technology_stack=stack,
    )


def _report(
    *,
    invited_count: int = 0,
    answered_count: int = 0,
    needs_help: tuple[CheckinParticipantDTO, ...] = (),
    answered_participants: tuple[CheckinParticipantDTO, ...] = (),
    achievements: tuple[PulseAchievementDTO, ...] = (),
    non_responders: tuple[CheckinParticipantDTO, ...] = (),
    learning_technologies: tuple[PulseTechnologyDTO, ...] = (),
) -> PulseReportDTO:
    return PulseReportDTO(
        week_start=date(2026, 10, 5),
        week_end=date(2026, 10, 11),
        report_date=date(2026, 10, 15),
        invited_count=invited_count,
        answered_count=answered_count,
        needs_help_count=len(needs_help),
        goals_achieved_count=sum(item.status == CheckinStatus.GOAL_ACHIEVED for item in answered_participants),
        visit_count=0,
        answered_delta=0,
        needs_help_delta=0,
        goals_achieved_delta=0,
        visits_delta=0,
        needs_help=needs_help,
        answered_participants=answered_participants,
        achievements=achievements,
        non_responders=non_responders,
        learning_technologies=learning_technologies,
    )


_EMOJI_PATTERN = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0e\ufe0f\u200d\u20e3]")
