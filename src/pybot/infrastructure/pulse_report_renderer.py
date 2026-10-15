"""Render the fixed, data-only weekly community pulse report as PDF."""

from __future__ import annotations

import re
from datetime import date
from html import escape
from pathlib import Path

from ..domain.services.community_checkins import CheckinStatus, get_checkin_deadline
from ..dto.community_checkins import CheckinParticipantDTO, PulseReportDTO

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "pulse_report.html"
_SILENT_WEEK_THRESHOLD = 4
_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_EMOJI_PATTERN = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0e\ufe0f\u200d\u20e3]")
_ACHIEVEMENT_ICON_PATTERN = re.compile(
    r'<svg class="award-icon" data-code="([^"]+)" viewBox="0 0 48 48" aria-hidden="true">.*?</svg>',
    re.DOTALL,
)
_ACHIEVEMENT_CODES = frozenset(
    {
        "first_step",
        "streak_4",
        "streak_8",
        "streak_16",
        "visits_10",
        "visits_25",
        "visits_50",
        "comeback",
        "top_week",
        "goal_done",
        "mentor",
    }
)
_STATUS_LABELS = {
    CheckinStatus.IN_PROGRESS: "В процессе",
    CheckinStatus.STUCK: "Застрял",
    CheckinStatus.NO_MOTIVATION: "Нет мотивации",
    CheckinStatus.GOAL_ACHIEVED: "Цель взята",
    CheckinStatus.NEWBIE: "Новичок",
    CheckinStatus.NO_SUBSTANTIVE_ANSWER: "Без ответа по существу",
}


def build_pulse_report_html(report: PulseReportDTO) -> str:
    """Fill the repository-owned HTML template with escaped report values only."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    achievement_icons = {match.group(1): match.group(0) for match in _ACHIEVEMENT_ICON_PATTERN.finditer(template)}
    needs_help, goals, other_responders = _split_responders(report)
    responded_user_ids = {item.user_id for item in (*needs_help, *goals, *other_responders)}
    non_responders = tuple(
        item for item in _unique_participants(report.non_responders) if item.user_id not in responded_user_ids
    )
    replacements = {
        "@@REPORT_PERIOD@@": _render_report_period(report),
        "@@KPI_CARDS@@": _render_kpis(report),
        "@@NEEDS_HELP_COUNT@@": _participant_count_label(len(needs_help)),
        "@@NEEDS_HELP@@": _render_participants(needs_help, kind="stuck"),
        "@@GOALS_COUNT@@": _participant_count_label(len(goals)),
        "@@GOALS_ACHIEVED@@": _render_participants(goals, kind="goal"),
        "@@ANSWERED_COUNT@@": _participant_count_label(len(other_responders)),
        "@@ANSWERED_PARTICIPANTS@@": _render_participants(other_responders, kind="progress"),
        "@@ACHIEVEMENTS@@": _render_achievements(report, achievement_icons),
        "@@NON_RESPONDER_COUNT@@": _participant_count_label(len(non_responders)),
        "@@NON_RESPONDERS@@": _render_non_responders(report, non_responders),
        "@@LEARNING_TECHNOLOGIES@@": _render_learning_technologies(report),
    }
    for token, replacement in replacements.items():
        template = template.replace(token, replacement)
    return _EMOJI_PATTERN.sub("", template)


def render_pulse_report_pdf(report: PulseReportDTO) -> bytes:
    """Render a PDF using the fixed template and repository-bundled font."""
    # Pango libraries are present in the Linux runtime image, not on every dev host.
    from weasyprint import HTML  # noqa: PLC0415

    return HTML(string=build_pulse_report_html(report), base_url=str(_TEMPLATE_PATH.parent)).write_pdf()


def _split_responders(
    report: PulseReportDTO,
) -> tuple[
    tuple[CheckinParticipantDTO, ...],
    tuple[CheckinParticipantDTO, ...],
    tuple[CheckinParticipantDTO, ...],
]:
    """Assign each responder to the highest-priority section exactly once."""
    answered = _unique_participants(report.answered_participants)
    answered_by_id = {item.user_id: item for item in answered}
    help_candidates = _unique_participants((*report.needs_help, *answered))
    needs_help = tuple(
        answered_by_id.get(item.user_id, item)
        for item in help_candidates
        if item.status in {CheckinStatus.STUCK, CheckinStatus.NO_MOTIVATION}
    )
    placed_user_ids = {item.user_id for item in needs_help}
    goals = tuple(
        item for item in answered if item.user_id not in placed_user_ids and item.status == CheckinStatus.GOAL_ACHIEVED
    )
    placed_user_ids.update(item.user_id for item in goals)
    other_responders = tuple(item for item in answered if item.user_id not in placed_user_ids)
    return needs_help, goals, other_responders


def _unique_participants(participants: tuple[CheckinParticipantDTO, ...]) -> tuple[CheckinParticipantDTO, ...]:
    """Preserve the first occurrence of each participant."""
    seen_user_ids: set[int] = set()
    unique: list[CheckinParticipantDTO] = []
    for participant in participants:
        if participant.user_id in seen_user_ids:
            continue
        seen_user_ids.add(participant.user_id)
        unique.append(participant)
    return tuple(unique)


def _render_report_period(report: PulseReportDTO) -> str:
    """Show the check-in closing date and the local report date in Russian."""
    checkin_date = get_checkin_deadline(report.week_start).date()
    checkin_label = _format_russian_date(checkin_date)
    report_label = _format_russian_date(report.report_date)
    return f"Чекин {checkin_label} · отчёт {report_label} {report.report_date.year}"


def _format_russian_date(value: date) -> str:
    """Return a date with a genitive Russian month name and no leading zero."""
    return f"{value.day} {_MONTHS_GENITIVE[value.month - 1]}"


def _render_kpis(report: PulseReportDTO) -> str:
    cards = (
        (report.answer_ratio, "Ответили на чекин", report.answered_delta, "к прошлой неделе", False),
        (str(report.needs_help_count), "Нужна помощь", report.needs_help_delta, "к прошлой неделе", True),
        (str(report.goals_achieved_count), "Цели достигнуты", report.goals_achieved_delta, "к прошлой неделе", False),
        (str(report.visit_count), "Посещений за 7 дней", report.visits_delta, "к предыдущим 7 дням", False),
    )
    return "\n".join(
        '<div class="kpi"><div class="value">'
        f'{escape(value)}</div><div class="label">{escape(label)}</div>'
        f'<div class="delta {_delta_class(delta, reverse=reverse)}">{_delta_text(delta, comparison)}</div></div>'
        for value, label, delta, comparison, reverse in cards
    )


def _render_participants(participants: tuple[CheckinParticipantDTO, ...], *, kind: str) -> str:
    if not participants:
        return '<div class="empty">Пока нет записей.</div>'

    cards: list[str] = []
    for participant in participants:
        name = escape(participant.display_name or "Имя не указано")
        username = escape(participant.mention)
        goal_text = escape(participant.goal_text or "Цель не указана")
        status_label = "Без ответа" if participant.status is None else _STATUS_LABELS[participant.status]
        status = escape(status_label)
        summary = escape(participant.summary or "Ответ не содержит краткого итога по теме.")
        card_lines = [
            f'<article class="card participant {kind}" data-user-id="{participant.user_id}">',
            '<div class="who">',
            f'<span class="name">{name}</span>',
            f'<span class="username">{username}</span>',
            "</div>",
        ]
        if participant.goal_text:
            card_lines.append(f'<div class="goal-line"><span>Цель:</span> {goal_text}</div>')
        card_lines.append(f'<div class="status-line"><span class="badge {kind}">{status}</span></div>')
        if participant.status == CheckinStatus.NO_SUBSTANTIVE_ANSWER:
            card_lines.append('<div class="annotation">Пометка: без ответа по существу.</div>')
        else:
            card_lines.append(f'<div class="summary">{summary}</div>')
        if participant.technology_stack:
            chips = []
            for item in participant.technology_stack:
                chip_class = "chip know" if item.level.value == "know" else "chip"
                level_label = "" if item.level.value == "know" else " · изучаю"
                chips.append(f'<span class="{chip_class}">{escape(item.technology + level_label)}</span>')
            card_lines.append('<div class="chips">' + "".join(chips) + "</div>")
        if participant.helper_names:
            helpers = ", ".join(escape(helper) for helper in participant.helper_names)
            card_lines.append(f'<div class="help">Могут помочь: {helpers}</div>')
        if participant.achievements:
            card_lines.append(
                f'<div class="participant-achievements">Достижения: {escape(", ".join(participant.achievements))}</div>'
            )
        card_lines.append(
            '<div class="foot">'
            f"<span>Посещений за 7 дней: <b>{participant.visit_count}</b></span>"
            f"<span>Текущая серия: <b>{participant.current_streak} {_week_word(participant.current_streak)}</b></span>"
            "</div>"
        )
        card_lines.append("</article>")
        cards.append("".join(card_lines))
    return '<div class="participant-grid">' + "".join(cards) + "</div>"


def _render_achievements(report: PulseReportDTO, achievement_icons: dict[str, str]) -> str:
    if not report.achievements:
        return '<div class="empty">На этой неделе новых достижений нет.</div>'
    entries = []
    for achievement in report.achievements:
        names = ", ".join(escape(name) for name in achievement.participant_names)
        icon_code = achievement.code if achievement.code in _ACHIEVEMENT_CODES else "first_step"
        icon_svg = achievement_icons[icon_code]
        entries.append(
            '<div class="card achievement">'
            f"{icon_svg}"
            f'<div class="ach-title">{escape(achievement.title)}</div>'
            f'<div class="foot">{names}</div></div>'
        )
    return '<div class="achievement-grid">' + "".join(entries) + "</div>"


def _render_non_responders(
    report: PulseReportDTO,
    non_responders: tuple[CheckinParticipantDTO, ...],
) -> str:
    participants = sorted(
        non_responders,
        key=lambda participant: participant.silent_weeks,
        reverse=True,
    )
    if not participants:
        if report.invited_count == 0:
            return '<div class="empty">На эту неделю приглашений не было.</div>'
        return '<div class="empty">Все участники ответили на чекин.</div>'
    rows: list[str] = []
    for participant in participants:
        silent_class = "silent-critical" if participant.silent_weeks >= _SILENT_WEEK_THRESHOLD else ""
        last_visit = participant.last_visit.strftime("%d.%m.%Y") if participant.last_visit else "нет данных"
        rows.append(
            f'<tr class="{silent_class}" data-user-id="{participant.user_id}">'
            f"<td>{escape(participant.mention)}</td>"
            f"<td>{last_visit}</td><td>{participant.silent_weeks}</td></tr>"
        )
    return (
        '<table class="table"><thead><tr><th>Участник</th><th>Последний визит</th>'
        "<th>без ответа, нед.</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_learning_technologies(report: PulseReportDTO) -> str:
    if not report.learning_technologies:
        return '<div class="empty">Сейчас никто ничего не изучает.</div>'
    highest = max(item.participant_count for item in report.learning_technologies)
    rows = []
    for item in report.learning_technologies:
        width = max(1, round(item.participant_count / highest * 100))
        rows.append(
            '<div class="bar">'
            f"<span>{escape(item.technology)}</span>"
            f'<div class="track"><div class="fill" style="width:{width}%"></div></div>'
            f"<b>{item.participant_count}</b></div>"
        )
    return '<div class="card bars">' + "".join(rows) + "</div>"


def _participant_count_label(count: int) -> str:
    """Pluralize a participant count for section headings."""
    last_two_digits = count % 100
    last_digit = count % 10
    noun = "участников"
    if last_two_digits not in {11, 12, 13, 14}:
        if last_digit == 1:
            noun = "участник"
        elif last_digit in {2, 3, 4}:
            noun = "участника"
    return f"{count} {noun}"


def _week_word(count: int) -> str:
    """Pluralize the participant's current week streak."""
    last_two_digits = count % 100
    last_digit = count % 10
    if last_two_digits not in {11, 12, 13, 14}:
        if last_digit == 1:
            return "неделя"
        if last_digit in {2, 3, 4}:
            return "недели"
    return "недель"


def _delta_class(delta: int, *, reverse: bool = False) -> str:
    if delta == 0:
        return ""
    is_positive = delta > 0
    if reverse:
        is_positive = not is_positive
    return "positive" if is_positive else "negative"


def _delta_text(delta: int, comparison: str) -> str:
    if delta > 0:
        return f"▲ +{delta} {comparison}"
    if delta < 0:
        return f"▼ {delta} {comparison}"
    return "без изменений"
