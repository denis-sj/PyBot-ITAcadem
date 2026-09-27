"""Texts and formatting functions for attendance accounting presentation."""

from __future__ import annotations

import html

from ...dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecordReadDTO,
    AttendanceUserSummaryDTO,
)

BTN_CONFIRM = "✅ Подтвердить"
BTN_EDIT_TIME = "✏️ Изменить время"
BTN_EDIT_USER = "👤 Назначить участника"
BTN_EXCLUDE = "🗑 Исключить строку"
BTN_CANCEL = "❌ Отменить импорт"
BTN_CLOSE = "✖ Закрыть"
MONTH_NAMES_RU: dict[int, str] = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}

MONTH_NAMES_RU_CAPITALIZED: dict[int, str] = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

_MEDALS: dict[int, str] = {
    1: "🥇 ",
    2: "🥈 ",
    3: "🥉 ",
}


def format_duration(minutes: int) -> str:
    """Format duration in minutes as human-friendly 'X ч Y мин' string."""
    if minutes <= 0:
        return "0 мин"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours > 0 and rem_minutes > 0:
        return f"{hours} ч {rem_minutes:02d} мин"
    if hours > 0:
        return f"{hours} ч"
    return f"{rem_minutes} мин"


def _format_ignored_date_block(block: AttendanceDraftDateBlockDTO) -> str:
    """Format ignored date block reason."""
    date_str = block.date.strftime("%d.%m.%Y")
    if block.ignore_reason == "дата уже обработана":
        import_part = f" (импорт #{block.processed_import_id})" if block.processed_import_id else ""
        return f"  ⚠️ {date_str} уже учтена{import_part}. Строки за эту дату не будут сохранены.\n"
    if block.ignore_reason == "будущая дата":
        return "  ⚠️ <i>Дата пропущена (будущая дата)</i>\n"
    reason = block.ignore_reason or "не учитывается"
    return f"  ⚠️ <i>Дата пропущена ({html.escape(reason)})</i>\n"


def _format_active_date_block(
    block: AttendanceDraftDateBlockDTO,
    start_counter: int,
) -> tuple[list[str], int, int, int, int]:
    """Format active date block rows and return counts (total, recognized, needs_review, minutes)."""
    if not block.items:
        return ["  <i>Нет распознанных строк</i>\n"], 0, 0, 0, 0

    lines: list[str] = []
    total = 0
    recognized = 0
    needs_review = 0
    minutes = 0

    for idx, item in enumerate(block.items, start=start_counter):
        total += 1
        if item.status == "recognized":
            recognized += 1
        else:
            needs_review += 1

        if not item.is_excluded:
            minutes += item.duration_minutes

        lines.append(_format_draft_item(item, idx))

    lines.append("")
    return lines, total, recognized, needs_review, minutes


def format_draft_preview(draft: AttendanceDraftDTO) -> str:
    """Render structured preview of recognized attendance sheet draft."""
    all_processed = bool(draft.dates) and all(
        b.is_ignored and b.ignore_reason == "дата уже обработана" for b in draft.dates
    )
    if all_processed:
        imports = [f"#{b.processed_import_id}" for b in draft.dates if b.processed_import_id]
        import_part = f" (импорт {', '.join(dict.fromkeys(imports))})" if imports else ""
        return (
            f"📸 <b>Предпросмотр листа посещаемости</b> (черновик #{draft.draft_id})\n\n"
            f"Все даты на фото уже учтены{import_part}. Сохранять нечего."
        )

    lines: list[str] = [f"📸 <b>Предпросмотр листа посещаемости</b> (черновик #{draft.draft_id})\n"]

    # Period warning if present
    period_warnings = [b.period_warning for b in draft.dates if not b.is_ignored and b.period_warning]
    for w in dict.fromkeys(period_warnings):
        lines.append(f"⚠️ <i>{html.escape(w)}</i>\n")

    total_items = 0
    recognized_items = 0
    needs_review_items = 0
    total_minutes = 0

    item_counter = 1
    for block in draft.dates:
        date_str = block.date.strftime("%d.%m.%Y")
        lines.append(f"📅 <b>Дата: {date_str}</b>")

        if block.is_ignored:
            lines.append(_format_ignored_date_block(block))
            continue

        block_lines, b_total, b_rec, b_review, b_min = _format_active_date_block(block, item_counter)
        lines.extend(block_lines)
        total_items += b_total
        recognized_items += b_rec
        needs_review_items += b_review
        total_minutes += b_min
        item_counter += len(block.items)

    lines.extend(_format_draft_summary(total_items, recognized_items, needs_review_items, total_minutes))
    return "\n".join(lines)


def _format_draft_item(item: AttendanceDraftItemDTO, counter: int) -> str:
    """Format a single item row in draft preview."""
    if item.is_excluded:
        status_icon = "🚫"
    elif item.status == "recognized":
        status_icon = "✅"
    else:
        status_icon = "⚠️"

    if item.matched_user_name:
        name_display = html.escape(item.matched_user_name)
    elif item.raw_name:
        name_display = f"❓ <i>«{html.escape(item.raw_name)}»</i>"
    else:
        name_display = "❓ <i>[Имя не распознано]</i>"

    if item.start_time and item.end_time:
        time_display = f"{item.start_time.strftime('%H:%M')}–{item.end_time.strftime('%H:%M')}"
    else:
        time_display = "время не указано"
    dur_str = format_duration(item.duration_minutes) if item.duration_minutes > 0 else "требует ввода времени"
    excluded_suffix = " <i>[исключено]</i>" if item.is_excluded else ""
    return f"  {counter}. {status_icon} {name_display}: {time_display} ({dur_str}){excluded_suffix}"


def _format_draft_summary(
    total_items: int,
    recognized_items: int,
    needs_review_items: int,
    total_minutes: int,
) -> list[str]:
    """Format summary statistics at bottom of draft preview."""
    lines = [
        "<b>📊 Итоги распознавания:</b>",
        f"• Всего строк: {total_items}",
        f"• Распознано: {recognized_items}",
    ]
    if needs_review_items > 0:
        lines.append(f"• Требует внимания: <b>{needs_review_items}</b> ⚠️")
    lines.append(f"• Общее время на листе: <b>{format_duration(total_minutes)}</b>")
    if needs_review_items > 0:
        lines.append("\n<i>Обратите внимание на строки с ⚠️. Назначьте участников или время перед подтверждением.</i>")
    return lines


def format_user_summary(summary: AttendanceUserSummaryDTO, month: int, year: int) -> str:
    """Format personal hours and club ranking for /my_hours command."""
    month_name = MONTH_NAMES_RU.get(month, "")
    if summary.visits_count == 0 or summary.total_minutes == 0 or summary.rank <= 0:
        return (
            f"⏱ <b>Ваши часы посещаемости за {month_name} {year}:</b>\n\n"
            f"👤 Участник: <b>{html.escape(summary.user_full_name)}</b>\n\n"
            "В этом месяце у вас пока нет посещений."
        )
    return (
        f"⏱ <b>Ваши часы посещаемости за {month_name} {year}:</b>\n\n"
        f"👤 Участник: <b>{html.escape(summary.user_full_name)}</b>\n"
        f"⏳ Налетанное время: <b>{format_duration(summary.total_minutes)}</b>\n"
        f"🏢 Количество визитов: <b>{summary.visits_count}</b>\n"
        f"🏆 Место в рейтинге: <b>#{summary.rank}</b> из {summary.total_participants}\n"
    )


def format_user_visits(visits: list[AttendanceRecordReadDTO], month: int, year: int) -> str:
    """Format list of personal visits for /my_visits command."""
    month_name = MONTH_NAMES_RU.get(month, "")
    lines = [f"📋 <b>Ваши посещения за {month_name} {year}:</b>\n"]
    if not visits:
        lines.append("<i>В этом месяце подтвержденных посещений пока нет.</i>")
        return "\n".join(lines)

    total_min = sum(v.duration_minutes for v in visits)
    for idx, v in enumerate(visits, 1):
        date_str = v.date.strftime("%d.%m.%Y")
        time_span = (
            f"{v.start_time.strftime('%H:%M')}–{v.end_time.strftime('%H:%M')}"
            if v.start_time and v.end_time
            else "без фиксации времени"
        )
        lines.append(f"{idx}. 📅 <b>{date_str}</b>: {time_span} — <b>{format_duration(v.duration_minutes)}</b>")

    lines.append(f"\nВсего: <b>{len(visits)}</b> визитов, <b>{format_duration(total_min)}</b>")
    return "\n".join(lines)


def format_user_rank(summary: AttendanceUserSummaryDTO, month: int, year: int) -> str:
    """Format user ranking for /my_rank command."""
    month_name = MONTH_NAMES_RU.get(month, "")
    if summary.visits_count == 0 or summary.total_minutes == 0 or summary.rank <= 0:
        return f"🏆 <b>Ваша позиция в клубе за {month_name} {year}:</b>\n\nВ этом месяце у вас пока нет посещений."
    return (
        f"🏆 <b>Ваша позиция в клубе за {month_name} {year}:</b>\n\n"
        f"Место: <b>#{summary.rank}</b> из {summary.total_participants}\n"
        f"Часов налета: <b>{format_duration(summary.total_minutes)}</b> ({summary.visits_count} посещ.)"
    )


def format_monthly_report(report: AttendanceMonthlyReportDTO, detailed: bool = False) -> str:
    """Format club monthly report for /report command."""
    month_title = MONTH_NAMES_RU_CAPITALIZED.get(report.month, f"Месяц {report.month}")
    lines: list[str] = [
        "📊 <b>Отчёт по часам посещаемости клуба</b>",
        f"Период: <b>{month_title} {report.year}</b>\n",
        f"• Всего часов клуба: <b>{format_duration(report.total_club_minutes)}</b>",
        f"• Активных участников: <b>{report.active_participants_count}</b>",
        f"• Среднее время на участника: <b>{format_duration(report.average_minutes_per_participant)}</b>\n",
        "<b>🏆 Рейтинг участников:</b>",
    ]

    if not report.rankings:
        lines.append("<i>За этот месяц посещений нет.</i>")
    else:
        for item in report.rankings:
            medal = _MEDALS.get(item.rank, f"{item.rank}. ")
            lines.append(
                f"{medal}<b>{html.escape(item.user_full_name)}</b>: {format_duration(item.total_minutes)} "
                f"({item.visits_count} виз.)"
            )

    if detailed and report.records:
        lines.append("\n<b>📋 Детализация посещений:</b>")
        for idx, rec in enumerate(report.records, 1):
            d_str = rec.date.strftime("%d.%m")
            time_part = (
                f"{rec.start_time.strftime('%H:%M')}–{rec.end_time.strftime('%H:%M')}"
                if rec.start_time and rec.end_time
                else "—"
            )
            rec_name = html.escape(rec.user_full_name)
            rec_dur = format_duration(rec.duration_minutes)
            lines.append(f"{idx}. {d_str} {rec_name} ({time_part}) — {rec_dur}")
    return "\n".join(lines)
