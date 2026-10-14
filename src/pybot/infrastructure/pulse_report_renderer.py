"""Render the fixed, data-only weekly community pulse report as PDF."""

from __future__ import annotations

from html import escape
from pathlib import Path

from ..domain.services.community_checkins import CheckinStatus
from ..dto.community_checkins import CheckinParticipantDTO, PulseReportDTO

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "pulse_report.html"
_SILENT_WEEK_THRESHOLD = 4
_STATUS_LABELS = {
    CheckinStatus.IN_PROGRESS: "В процессе",
    CheckinStatus.STUCK: "Застрял",
    CheckinStatus.NO_MOTIVATION: "Нет мотивации",
    CheckinStatus.GOAL_ACHIEVED: "Цель достигнута",
    CheckinStatus.NEWBIE: "Новичок",
    CheckinStatus.NO_SUBSTANTIVE_ANSWER: "Без ответа по существу",
}


def build_pulse_report_html(report: PulseReportDTO) -> str:
    """Fill the repository-owned HTML template with escaped report values only."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "@@WEEK_LABEL@@": f"{report.week_start:%d.%m.%Y} — {report.week_end:%d.%m.%Y}",
        "@@KPI_CARDS@@": _render_kpis(report),
        "@@NEEDS_HELP_COUNT@@": str(report.needs_help_count),
        "@@NEEDS_HELP@@": _render_participants(report.needs_help, kind="stuck"),
        "@@GOALS_ACHIEVED@@": _render_participants(
            tuple(item for item in report.answered_participants if item.status == CheckinStatus.GOAL_ACHIEVED),
            kind="goal",
        ),
        "@@ANSWERED_COUNT@@": str(report.answered_count),
        "@@ANSWERED_PARTICIPANTS@@": _render_participants(report.answered_participants, kind="progress"),
        "@@ACHIEVEMENTS@@": _render_achievements(report),
        "@@NON_RESPONDER_COUNT@@": str(len(report.non_responders)),
        "@@NON_RESPONDERS@@": _render_non_responders(report),
        "@@LEARNING_TECHNOLOGIES@@": _render_learning_technologies(report),
    }
    for token, replacement in replacements.items():
        template = template.replace(token, replacement)
    return template


def render_pulse_report_pdf(report: PulseReportDTO) -> bytes:
    """Render a PDF using the fixed template and repository-bundled font."""
    # Pango libraries are present in the Linux runtime image, not on every dev host.
    from weasyprint import HTML  # noqa: PLC0415

    return HTML(string=build_pulse_report_html(report), base_url=str(_TEMPLATE_PATH.parent)).write_pdf()


def _render_kpis(report: PulseReportDTO) -> str:
    cards = (
        (report.answer_ratio, "Ответили на чекин", report.answered_delta),
        (str(report.needs_help_count), "Нужна помощь", report.needs_help_delta),
        (str(report.goals_achieved_count), "Цели достигнуты", report.goals_achieved_delta),
        (str(report.visit_count), "Посещений за неделю", report.visits_delta),
    )
    return "\n".join(
        '<div class="kpi"><div class="value">'
        f'{escape(value)}</div><div class="label">{escape(label)}</div>'
        f'<div class="delta {_delta_class(delta)}">{_delta_text(delta)}</div></div>'
        for value, label, delta in cards
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
        summary = escape(participant.summary or "Ответ без краткого итога.")
        card_lines = [f'<article class="card {kind}">', '<div class="who">', f'<span class="name">{name}</span>']
        card_lines.extend((f'<span class="username">{username}</span>', "</div>"))
        if participant.goal_text:
            card_lines.append(f'<div class="line muted">Цель: {goal_text}</div>')
        card_lines.append(f'<div class="line"><span class="badge {kind}">{status}</span></div>')
        card_lines.append(f'<div class="summary">{summary}</div>')
        if participant.helper_names:
            helpers = ", ".join(escape(helper) for helper in participant.helper_names)
            card_lines.append(f'<div class="help">Могут помочь: {helpers}</div>')
        if participant.achievements:
            card_lines.append(f'<div class="foot">🏅 {escape(", ".join(participant.achievements))}</div>')
        card_lines.append("</article>")
        cards.append("".join(card_lines))
    return '<div class="grid">' + "".join(cards) + "</div>"


def _render_achievements(report: PulseReportDTO) -> str:
    if not report.achievements:
        return '<div class="empty">На этой неделе новых достижений нет.</div>'
    entries = []
    for achievement in report.achievements:
        names = ", ".join(escape(name) for name in achievement.participant_names)
        entries.append(
            '<div class="card achievement">'
            f'<div class="icon">{escape(achievement.icon)}</div>'
            f'<div class="ach-title">{escape(achievement.title)}</div>'
            f'<div class="foot">{names}</div></div>'
        )
    return '<div class="achievement-grid">' + "".join(entries) + "</div>"


def _render_non_responders(report: PulseReportDTO) -> str:
    if not report.non_responders:
        if report.invited_count == 0:
            return '<div class="empty">На эту неделю приглашений не было.</div>'
        return '<div class="empty">Все участники ответили на чекин.</div>'
    rows: list[str] = []
    for participant in report.non_responders:
        silent_class = "silent" if participant.silent_weeks >= _SILENT_WEEK_THRESHOLD else ""
        last_visit = participant.last_visit.strftime("%d.%m.%Y") if participant.last_visit else "нет данных"
        rows.append(
            f'<tr class="{silent_class}"><td>{escape(participant.display_name or "Имя не указано")}</td>'
            f"<td>{last_visit}</td><td>{participant.visit_count}</td></tr>"
        )
    return (
        '<table class="table"><thead><tr><th>Участник</th><th>Последний визит</th><th>За неделю</th></tr></thead>'
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


def _delta_class(delta: int) -> str:
    return "positive" if delta > 0 else "negative" if delta < 0 else ""


def _delta_text(delta: int) -> str:
    if delta > 0:
        return f"▲ +{delta} к прошлой неделе"
    if delta < 0:
        return f"▼ {delta} к прошлой неделе"
    return "без изменений"
