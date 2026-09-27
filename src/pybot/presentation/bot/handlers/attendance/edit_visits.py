"""Admin handlers for /edit_visits interactive navigation and actions (Screens 1 to 4)."""

from __future__ import annotations

import re
from datetime import datetime, time

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core.config import YEKATERINBURG_TZ, AppSettings
from .....domain.exceptions import AttendanceDomainError
from .....services.attendance_service import AttendanceService
from ....texts.attendance import (
    format_edit_visits_dates_screen,
    format_edit_visits_record_card,
    format_edit_visits_user_screen,
    format_edit_visits_users_list,
)
from .keyboards import (
    EditVisitsCB,
    get_edit_visits_cancel_keyboard,
    get_edit_visits_confirm_delete_keyboard,
    get_edit_visits_confirm_manual_keyboard,
    get_edit_visits_confirm_time_keyboard,
    get_edit_visits_month_dates_keyboard,
    get_edit_visits_record_keyboard,
    get_edit_visits_user_keyboard,
    get_edit_visits_users_keyboard,
)
from .states import EditVisitsSG

attendance_edit_visits_router = Router(name="attendance_edit_visits")

_TIME_STRICT_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)[-–—]([01]\d|2[0-3]):([0-5]\d)$")


def _parse_time_strict(raw: str) -> tuple[time, time] | None:
    """Parse strictly validated HH:MM-HH:MM string, returning None if invalid."""
    match = _TIME_STRICT_REGEX.match(raw.strip())
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    t1 = time(h1, m1)
    t2 = time(h2, m2)
    return t1, t2


# ============ SCREEN 1: PARTICIPANTS LIST ============


@attendance_edit_visits_router.message(
    Command("edit_visits"),
    flags={"role": "Admin", "rate_limit": "moderate"},
)
async def cmd_edit_visits(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Entrypoint for /edit_visits command."""
    if not message.from_user:
        return

    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Команда доступна только в личном чате с ботом.")
        return

    await state.set_state(EditVisitsSG.waiting_for_user_number)
    await state.update_data(page=0)

    users = await attendance_service.get_sorted_participants()
    text = format_edit_visits_users_list(users, page=0)
    keyboard = get_edit_visits_users_keyboard(len(users), page=0)
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@attendance_edit_visits_router.message(
    EditVisitsSG.waiting_for_user_number,
    flags={"role": "Admin"},
)
async def msg_input_user_number(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Handle text input of user number on Screen 1."""
    raw_text = (message.text or "").strip()
    if not raw_text.isdigit():
        await message.answer("Введите номер участника числом.", parse_mode=ParseMode.HTML)
        return

    number = int(raw_text)
    users = await attendance_service.get_sorted_participants()
    total_users = len(users)

    if number < 1 or number > total_users:
        await message.answer(
            f"Нет участника с номером {number}. Введите число от 1 до {total_users}.",
            parse_mode=ParseMode.HTML,
        )
        return

    selected_user = users[number - 1]
    await state.clear()

    # Transition to Screen 2 (User details)
    months = await attendance_service.get_user_visit_months(selected_user.id)
    text = format_edit_visits_user_screen(selected_user, has_visits=bool(months))
    keyboard = get_edit_visits_user_keyboard(selected_user.id, months)
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


# ============ SCREEN 1 & 2 NAVIGATION ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "page"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_page(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Handle pagination on Screen 1 (users list)."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    users = await attendance_service.get_sorted_participants()
    text = format_edit_visits_users_list(users, page=callback_data.page)
    keyboard = get_edit_visits_users_keyboard(len(users), page=callback_data.page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "users"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_back_to_users(
    callback: CallbackQuery,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Return to Screen 1 from Screen 2, restoring FSM state."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.set_state(EditVisitsSG.waiting_for_user_number)
    users = await attendance_service.get_sorted_participants()
    text = format_edit_visits_users_list(users, page=0)
    keyboard = get_edit_visits_users_keyboard(len(users), page=0)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "user"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_open_user(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 2 for a specific user (or return from Screen 3 / cancel manual add)."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.clear()
    user = await attendance_service.get_user_by_id(callback_data.user_id)
    if user is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    months = await attendance_service.get_user_visit_months(user.id)
    text = format_edit_visits_user_screen(user, has_visits=bool(months))
    keyboard = get_edit_visits_user_keyboard(user.id, months)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


# ============ SCREEN 3: DATES OF MONTH ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "month"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_open_month(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 3: visit dates for the user in the selected month."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.clear()
    user = await attendance_service.get_user_by_id(callback_data.user_id)
    if user is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    visits = await attendance_service.get_user_monthly_visits(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
    )
    text = format_edit_visits_dates_screen(user, year=callback_data.year, month=callback_data.month)
    keyboard = get_edit_visits_month_dates_keyboard(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        records=visits,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


# ============ SCREEN 4: RECORD CARD ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "record"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_open_record(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 4: single visit details card."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.clear()
    record = await attendance_service.get_attendance_record(callback_data.record_id)
    if record is None:
        await callback.answer("Запись посещения не найдена", show_alert=True)
        return

    user = record.user or await attendance_service.get_user_by_id(callback_data.user_id)
    if user is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    history = await attendance_service.get_record_change_history(user.id, record.date)
    text = format_edit_visits_record_card(user, record, history=history)
    keyboard = get_edit_visits_record_keyboard(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        record=record,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


# ============ ACTION 1: EDIT VISIT TIME (SECTION 6.6) ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "start_edit_time"),
    flags={"role": "Admin"},
)
async def cb_start_edit_time(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Prompt administrator for new time range."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    record = await attendance_service.get_attendance_record(callback_data.record_id)
    if record is None:
        await callback.answer("Запись посещения не найдена", show_alert=True)
        return

    await state.set_state(EditVisitsSG.waiting_for_edit_time)
    await state.update_data(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        record_id=callback_data.record_id,
    )

    text = (
        "✏️ <b>Изменение времени посещения</b>\n\n"
        "Введите новое время в формате <code>ЧЧ:ММ-ЧЧ:ММ</code> (например, <code>12:24-15:32</code>):"
    )
    keyboard = get_edit_visits_cancel_keyboard(
        action="record",
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        record_id=callback_data.record_id,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.message(
    EditVisitsSG.waiting_for_edit_time,
    flags={"role": "Admin"},
)
async def msg_input_edit_time(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Handle text input of new time with strict validation (6.9)."""
    data = await state.get_data()
    user_id = data["user_id"]
    year = data["year"]
    month = data["month"]
    record_id = data["record_id"]

    raw_text = (message.text or "").strip()
    parsed = _parse_time_strict(raw_text)
    if parsed is None:
        keyboard = get_edit_visits_cancel_keyboard(
            action="record",
            user_id=user_id,
            year=year,
            month=month,
            record_id=record_id,
        )
        await message.answer(
            "⚠️ Неверный формат. Пример: 12:24-15:32",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    start_time, end_time = parsed
    if end_time <= start_time:
        keyboard = get_edit_visits_cancel_keyboard(
            action="record",
            user_id=user_id,
            year=year,
            month=month,
            record_id=record_id,
        )
        await message.answer(
            "⚠️ Время окончания должно быть позже начала.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    record = await attendance_service.get_attendance_record(record_id)
    if record is None:
        await state.clear()
        await message.answer("⚠️ Запись посещения не найдена.")
        return

    if record.start_time == start_time and record.end_time == end_time:
        keyboard = get_edit_visits_cancel_keyboard(
            action="record",
            user_id=user_id,
            year=year,
            month=month,
            record_id=record_id,
        )
        await message.answer(
            "⚠️ Новое время совпадает с текущим.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(
        new_start_time=start_time.strftime("%H:%M"),
        new_end_time=end_time.strftime("%H:%M"),
    )

    old_str = (
        f"{record.start_time.strftime('%H:%M')}–{record.end_time.strftime('%H:%M')}"
        if record.start_time and record.end_time
        else "без времени"
    )
    new_str = f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}"
    text = f"Было: {old_str} → Станет: {new_str}\n\nСохранить изменения?"
    keyboard = get_edit_visits_confirm_time_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        record_id=record_id,
    )
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "save_time"),
    flags={"role": "Admin"},
)
async def cb_save_time(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Save confirmed time modification, update record card, and notify user."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    data = await state.get_data()
    await state.clear()

    start_str = data.get("new_start_time")
    end_str = data.get("new_end_time")
    if not start_str or not end_str:
        await callback.answer("Данные времени устарели", show_alert=True)
        return

    h1, m1 = map(int, start_str.split(":"))
    h2, m2 = map(int, end_str.split(":"))
    new_start = time(h1, m1)
    new_end = time(h2, m2)

    admin_name = callback.from_user.full_name or callback.from_user.first_name or "Администратор"

    try:
        updated_record = await attendance_service.edit_visit_time(
            record_id=callback_data.record_id,
            new_start_time=new_start,
            new_end_time=new_end,
            admin_tg_id=callback.from_user.id,
            admin_name=admin_name,
        )

        user = updated_record.user or await attendance_service.get_user_by_id(callback_data.user_id)
        if user is None:
            await callback.answer("Участник не найден", show_alert=True)
            return

        history = await attendance_service.get_record_change_history(user.id, updated_record.date)
        text = format_edit_visits_record_card(user, updated_record, history=history)
        keyboard = get_edit_visits_record_keyboard(
            user_id=callback_data.user_id,
            year=callback_data.year,
            month=callback_data.month,
            record=updated_record,
        )
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer("Время успешно обновлено!")
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)


# ============ ACTION 2: DELETE VISIT (SECTION 6.7) ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "prompt_delete"),
    flags={"role": "Admin"},
)
async def cb_prompt_delete(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Prompt administrator with deletion confirmation."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    record = await attendance_service.get_attendance_record(callback_data.record_id)
    if record is None:
        await callback.answer("Запись посещения не найдена", show_alert=True)
        return

    d_str = record.date.strftime("%d.%m.%Y")
    t_str = (
        f"{record.start_time.strftime('%H:%M')}–{record.end_time.strftime('%H:%M')}"
        if record.start_time and record.end_time
        else "без времени"
    )
    text = f"Удалить посещение {d_str} ({t_str})?"
    keyboard = get_edit_visits_confirm_delete_keyboard(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        record_id=callback_data.record_id,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "confirm_delete"),
    flags={"role": "Admin"},
)
async def cb_confirm_delete(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Delete visit record, notify participant, and return to Screen 3 or Screen 2."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    admin_name = callback.from_user.full_name or callback.from_user.first_name or "Администратор"

    try:
        user_id, visit_date, _ = await attendance_service.delete_visit(
            record_id=callback_data.record_id,
            admin_tg_id=callback.from_user.id,
            admin_name=admin_name,
        )

        user = await attendance_service.get_user_by_id(user_id)
        if user is None:
            await callback.answer("Участник не найден", show_alert=True)
            return

        # Check remaining visits in that month
        visits = await attendance_service.get_user_monthly_visits(
            user_id=user_id,
            year=visit_date.year,
            month=visit_date.month,
        )
        if visits:
            # Return to Screen 3 (Dates)
            text = format_edit_visits_dates_screen(user, year=visit_date.year, month=visit_date.month)
            keyboard = get_edit_visits_month_dates_keyboard(
                user_id=user_id,
                year=visit_date.year,
                month=visit_date.month,
                records=visits,
            )
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            # Month no longer has visits! Return to Screen 2
            months = await attendance_service.get_user_visit_months(user_id)
            text = format_edit_visits_user_screen(user, has_visits=bool(months))
            keyboard = get_edit_visits_user_keyboard(user_id, months)
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

        await callback.answer("Посещение удалено")
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)


# ============ ACTION 3: MANUAL VISIT ADD (SECTION 6.8) ============


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "start_manual"),
    flags={"role": "Admin"},
)
async def cb_start_manual(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
) -> None:
    """Prompt administrator for visit date."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.set_state(EditVisitsSG.waiting_for_manual_date)
    await state.update_data(user_id=callback_data.user_id)

    text = (
        "📅 <b>Добавление посещения вручную</b>\n\n"
        "Введите дату посещения в формате <code>ДД.ММ.ГГГГ</code> (например, <code>25.09.2026</code>):"
    )
    keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=callback_data.user_id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.message(
    EditVisitsSG.waiting_for_manual_date,
    flags={"role": "Admin"},
)
async def msg_input_manual_date(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
    settings: FromDishka[AppSettings],
) -> None:
    """Handle date input for manual visit creation."""
    data = await state.get_data()
    user_id = data["user_id"]

    raw_text = (message.text or "").strip()
    try:
        visit_date = datetime.strptime(raw_text, "%d.%m.%Y").date()
    except ValueError:
        keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
        await message.answer(
            "⚠️ Неверный формат даты. Пример: 25.09.2026",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    today = datetime.now(YEKATERINBURG_TZ).date()
    if visit_date > today:
        keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
        await message.answer(
            "⚠️ Нельзя добавить посещение в будущую дату.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    # Check user doesn't already have visit on that date
    has_visit = await attendance_service.has_user_visit_on_date(user_id, visit_date)
    if has_visit:
        keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
        await message.answer(
            "⚠️ У участника уже есть посещение на эту дату.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(visit_date=visit_date.strftime("%d.%m.%Y"))
    await state.set_state(EditVisitsSG.waiting_for_manual_time)

    text = (
        "⏱ <b>Время посещения</b>\n\n"
        "Введите время в формате <code>ЧЧ:ММ-ЧЧ:ММ</code> (например, <code>18:00-20:00</code>):"
    )
    keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@attendance_edit_visits_router.message(
    EditVisitsSG.waiting_for_manual_time,
    flags={"role": "Admin"},
)
async def msg_input_manual_time(
    message: Message,
    state: FSMContext,
) -> None:
    """Handle time input for manual visit creation with strict validation (6.9)."""
    data = await state.get_data()
    user_id = data["user_id"]
    date_str = data["visit_date"]

    raw_text = (message.text or "").strip()
    parsed = _parse_time_strict(raw_text)
    if parsed is None:
        keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
        await message.answer(
            "⚠️ Неверный формат. Пример: 12:24-15:32",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    start_time, end_time = parsed
    if end_time <= start_time:
        keyboard = get_edit_visits_cancel_keyboard(action="user", user_id=user_id)
        await message.answer(
            "⚠️ Время окончания должно быть позже начала.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(
        start_time=start_time.strftime("%H:%M"),
        end_time=end_time.strftime("%H:%M"),
    )

    t_str = f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}"
    text = f"Добавить посещение {date_str}: {t_str}?"
    keyboard = get_edit_visits_confirm_manual_keyboard(user_id=user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "save_manual"),
    flags={"role": "Admin"},
)
async def cb_save_manual(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Persist manual visit, notify participant, and open Screen 4 (record card)."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    data = await state.get_data()
    await state.clear()

    date_str = data.get("visit_date")
    start_str = data.get("start_time")
    end_str = data.get("end_time")
    if not date_str or not start_str or not end_str:
        await callback.answer("Данные посещения устарели", show_alert=True)
        return

    visit_date = datetime.strptime(date_str, "%d.%m.%Y").date()
    h1, m1 = map(int, start_str.split(":"))
    h2, m2 = map(int, end_str.split(":"))
    start_time = time(h1, m1)
    end_time = time(h2, m2)

    admin_name = callback.from_user.full_name or callback.from_user.first_name or "Администратор"

    try:
        record = await attendance_service.create_manual_visit(
            user_id=callback_data.user_id,
            visit_date=visit_date,
            start_time=start_time,
            end_time=end_time,
            admin_tg_id=callback.from_user.id,
            admin_name=admin_name,
        )

        user = record.user or await attendance_service.get_user_by_id(callback_data.user_id)
        if user is None:
            await callback.answer("Участник не найден", show_alert=True)
            return

        history = await attendance_service.get_record_change_history(user.id, record.date)
        text = format_edit_visits_record_card(user, record, history=history)
        keyboard = get_edit_visits_record_keyboard(
            user_id=callback_data.user_id,
            year=visit_date.year,
            month=visit_date.month,
            record=record,
        )
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer("Посещение успешно добавлено!")
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
