"""Admin handlers for /edit_visits interactive navigation (Screens 1 to 4)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....services.attendance_service import AttendanceService
from ....texts.attendance import (
    format_edit_visits_dates_screen,
    format_edit_visits_record_card,
    format_edit_visits_user_screen,
    format_edit_visits_users_list,
)
from .keyboards import (
    EditVisitsCB,
    get_edit_visits_month_dates_keyboard,
    get_edit_visits_record_keyboard,
    get_edit_visits_user_keyboard,
    get_edit_visits_users_keyboard,
)
from .states import EditVisitsSG

attendance_edit_visits_router = Router(name="attendance_edit_visits")


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


# ============ CALLBACK NAVIGATION ============


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
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 2 for a specific user (or return from Screen 3)."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    user = await attendance_service.get_user_by_id(callback_data.user_id)
    if user is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    months = await attendance_service.get_user_visit_months(user.id)
    text = format_edit_visits_user_screen(user, has_visits=bool(months))
    keyboard = get_edit_visits_user_keyboard(user.id, months)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "month"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_open_month(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 3: visit dates for the user in the selected month."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

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


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "record"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_open_record(
    callback: CallbackQuery,
    callback_data: EditVisitsCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open Screen 4: single visit details card."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    record = await attendance_service.get_attendance_record(callback_data.record_id)
    if record is None:
        await callback.answer("Запись посещения не найдена", show_alert=True)
        return

    user = record.user or await attendance_service.get_user_by_id(callback_data.user_id)
    if user is None:
        await callback.answer("Участник не найден", show_alert=True)
        return

    text = format_edit_visits_record_card(user, record)
    keyboard = get_edit_visits_record_keyboard(
        user_id=callback_data.user_id,
        year=callback_data.year,
        month=callback_data.month,
        record=record,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@attendance_edit_visits_router.callback_query(
    EditVisitsCB.filter(F.action == "action"),
    flags={"role": "Admin"},
)
async def cb_edit_visits_action_placeholder(callback: CallbackQuery) -> None:
    """Handle placeholder actions for Screen 2/4 (add visit, edit time, delete)."""
    await callback.answer("Действие доступно в следующем обновлении", show_alert=True)
