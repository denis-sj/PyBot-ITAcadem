"""Texts and formatting functions for attendance accounting presentation."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, time
from typing import Any

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
BTN_TO_MONTHS = "← К месяцам"
BTN_TO_USERS = "← К списку участников"
BTN_ADD_VISIT = "➕ Добавить посещение"
BTN_TO_DATES = "← К датам"
BTN_EDIT_VISIT_TIME = "🕒 Изменить время"
BTN_DELETE_VISIT = "🗑 Удалить"
MY_VISITS_CHOOSE_MONTH = "📅 <b>Ваши посещения</b>\n\nВыберите месяц:"
MY_VISITS_NO_VISITS = "У вас пока нет подтверждённых посещений."
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
        import_part = f" (Номер фото #{block.processed_import_id})" if block.processed_import_id else ""
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
        import_part = f" (Номер фото {', '.join(dict.fromkeys(imports))})" if imports else ""
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


def _format_draft_item_name(item: AttendanceDraftItemDTO) -> str:
    """Format a participant name or its recognition placeholder."""
    if item.matched_user_name:
        return html.escape(item.matched_user_name)
    if item.raw_name:
        return f"❓ <i>«{html.escape(item.raw_name)}»</i>"
    return "❓ <i>[Имя не распознано]</i>"


def _format_draft_item(item: AttendanceDraftItemDTO, counter: int) -> str:
    """Format a single item row in draft preview."""
    name_display = _format_draft_item_name(item)
    if item.is_empty_time:
        return f"  {counter}. ➖ {name_display}: N"

    if item.is_excluded:
        status_icon = "🚫"
    elif item.status == "recognized":
        status_icon = "✅"
    else:
        status_icon = "⚠️"

    if item.start_time and item.end_time:
        time_display = f"{item.start_time.strftime('%H:%M')}–{item.end_time.strftime('%H:%M')}"
    elif item.time_raw:
        time_display = f"на листе «{html.escape(item.time_raw)}»"
    else:
        time_display = "время не указано"
    dur_str = format_duration(item.duration_minutes) if item.duration_minutes > 0 else "требует ввода времени"
    if item.exclusion_reason:
        excluded_suffix = f" <i>[исключено]</i>\n     ↳ <i>{html.escape(item.exclusion_reason)}</i>"
    elif item.is_excluded:
        excluded_suffix = " <i>[исключено]</i>"
    else:
        excluded_suffix = ""
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


def format_import_notification(
    import_id: int,
    user_visits: Sequence[tuple[date, time, time, int]],
) -> str:
    """Format personal notification message for a participant after attendance import confirmation."""
    sorted_visits = sorted(user_visits, key=lambda v: v[0])
    lines: list[str] = []
    if len(sorted_visits) == 1:
        lines.append("✅ <b>Добавлено посещение:</b>\n")
    else:
        lines.append("✅ <b>Добавлены посещения:</b>\n")

    for v_date, start_t, end_t, dur in sorted_visits:
        d_str = v_date.strftime("%d.%m.%Y")
        t_str = f"{start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}"
        dur_str = format_duration(dur)
        lines.append(f"{d_str} — {t_str} ({dur_str})")

    lines.append(f"\nНомер фото #{import_id}. Нажмите кнопку ниже, чтобы открыть фото. Все посещения: /my_visits")
    return "\n".join(lines)


def format_my_visits_month_report(
    year: int,
    month: int,
    summary: AttendanceUserSummaryDTO,
    visits: list[AttendanceRecordReadDTO],
) -> str:
    """Format personal month report for /my_visits Screen 2."""
    month_title = MONTH_NAMES_RU_CAPITALIZED.get(month, f"Месяц {month}")
    rank_display = str(summary.rank) if summary.rank > 0 else "—"
    total_dur_str = format_duration(summary.total_minutes)

    header_stats = (
        f"Посещений: {summary.visits_count} · Всего: {total_dur_str} "
        f"· Место: {rank_display} из {summary.total_participants}\n"
    )
    lines = [
        f"📅 <b>{month_title} {year}</b>",
        header_stats,
    ]

    sorted_visits = sorted(visits, key=lambda v: v.date)
    for v in sorted_visits:
        d_str = v.date.strftime("%d.%m")
        time_part = (
            f"{v.start_time.strftime('%H:%M')}–{v.end_time.strftime('%H:%M')}"
            if v.start_time and v.end_time
            else "без фиксации времени"
        )
        dur_part = format_duration(v.duration_minutes)
        source_part = f"Номер фото #{v.import_id}" if v.import_id else "добавлено вручную"
        mod_part = " · время изменено администратором" if getattr(v, "is_time_modified", False) else ""
        lines.append(f"{d_str} — {time_part} ({dur_part}) · {source_part}{mod_part}")

    return "\n".join(lines)


def format_user_full_name(user: Any) -> str:
    """Format full name as 'Фамилия Имя Отчество'."""
    parts = [getattr(user, "last_name", None), getattr(user, "first_name", None), getattr(user, "patronymic", None)]
    return " ".join(p.strip() for p in parts if p and p.strip())


def format_user_short_name(user: Any) -> str:
    """Format user name as 'Фамилия И. О.' or 'Фамилия И.' if no patronymic."""
    last = getattr(user, "last_name", None) or ""
    first = getattr(user, "first_name", None) or ""
    patr = getattr(user, "patronymic", None) or ""
    f_init = f"{first[0]}." if first else ""
    p_init = f" {patr[0]}." if patr else ""
    full_init = f"{f_init}{p_init}".strip()
    return f"{last} {full_init}".strip() if full_init else last


def format_edit_visits_users_list(users: Sequence[Any], page: int = 0, page_size: int = 40) -> str:
    """Format Screen 1: quote-wrapped list of all registered participants with 1-based indexing."""
    start = page * page_size
    page_users = users[start : start + page_size]

    lines = ["<blockquote>"]
    for idx, u in enumerate(page_users, start=start + 1):
        name = format_user_full_name(u)
        lines.append(f"{idx}. {html.escape(name)}")
    lines.append("</blockquote>")
    lines.append("\nВведите номер участника.")
    return "\n".join(lines)


def format_edit_visits_user_screen(user: Any, has_visits: bool) -> str:
    """Format Screen 2: participant card with month selection or no-visits notice."""
    full_name = html.escape(format_user_full_name(user))
    if not has_visits:
        return f"👤 <b>{full_name}</b>\n\nУ участника пока нет посещений."
    return f"👤 <b>{full_name}</b>\n\nВыберите месяц или добавьте посещение:"


def format_edit_visits_dates_screen(user: Any, year: int, month: int) -> str:
    """Format Screen 3: header for choosing a visit date in the selected month."""
    short_name = html.escape(format_user_short_name(user))
    month_name = MONTH_NAMES_RU_CAPITALIZED.get(month, f"Месяц {month}")
    return f"👤 <b>{short_name} · {month_name} {year}</b>\n\nВыберите дату посещения:"


def format_edit_visits_record_card(user: Any, record: Any, history: Sequence[Any] = ()) -> str:
    """Format Screen 4: single visit details card."""
    short_name = html.escape(format_user_short_name(user))
    date_str = record.date.strftime("%d.%m.%Y")
    time_part = (
        f"{record.start_time.strftime('%H:%M')}–{record.end_time.strftime('%H:%M')}"
        if record.start_time and record.end_time
        else "без фиксации времени"
    )
    dur_str = format_duration(record.duration_minutes)
    source_part = f"Номер фото #{record.import_id}" if getattr(record, "import_id", None) else "добавлено вручную"

    lines = [
        f"👤 <b>{short_name} · {date_str}</b>",
        f"Время: <b>{time_part}</b> ({dur_str})",
        f"Источник: {source_part}",
    ]

    if history:
        lines.append("\n<b>История изменений:</b>")
        for h in history:
            created_str = h.created_at.strftime("%d.%m.%Y %H:%M")
            admin_name = html.escape(h.admin_name)
            if h.action == "edit_time":
                old_val = f" (было {html.escape(h.old_value)})" if h.old_value else ""
                lines.append(f"• Изменено {created_str}, админ {admin_name}{old_val}")
            elif h.action == "manual_add":
                lines.append(f"• Добавлено вручную {created_str}, админ {admin_name}")
            elif h.action == "delete":
                lines.append(f"• Удалено {created_str}, админ {admin_name}")

    return "\n".join(lines)
