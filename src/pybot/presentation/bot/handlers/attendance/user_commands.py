"""User-facing Telegram commands for personal attendance hours and statistics."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.config import YEKATERINBURG_TZ, AppSettings
from .....domain.exceptions import AttendanceDomainError, AttendancePhotoAccessDeniedError
from .....services import UserService
from .....services.attendance_service import AttendanceService
from ....texts.attendance import (
    MY_VISITS_CHOOSE_MONTH,
    MY_VISITS_NO_VISITS,
    format_my_visits_month_report,
    format_user_rank,
    format_user_summary,
)
from .keyboards import (
    AttendancePhotoCB,
    MyVisitsCB,
    get_my_visits_month_report_keyboard,
    get_my_visits_months_keyboard,
)

attendance_user_router = Router(name="attendance_user")

_USER_NOT_REGISTERED_MSG = (
    "ℹ️ <b>Вы ещё не зарегистрированы в системе клуба.</b>\n\n"
    "Пожалуйста, отправьте команду /start, чтобы пройти короткую регистрацию."
)


@attendance_user_router.message(Command("my_hours"), flags={"rate_limit": "cheap"})
async def cmd_my_hours(
    message: Message,
    user_service: FromDishka[UserService],
    attendance_service: FromDishka[AttendanceService],
    settings: FromDishka[AppSettings],
) -> None:
    """Show personal attendance hours and ranking in the club for the current month."""
    if not message.from_user:
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(_USER_NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return

    now = datetime.now(YEKATERINBURG_TZ)

    try:
        summary = await attendance_service.get_user_monthly_summary(
            user_id=user.id,
            year=now.year,
            month=now.month,
        )
        text = format_user_summary(summary, month=now.month, year=now.year)
        await message.answer(text, parse_mode=ParseMode.HTML)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")


@attendance_user_router.message(Command("my_visits"), flags={"rate_limit": "cheap"})
async def cmd_my_visits(
    message: Message,
    user_service: FromDishka[UserService],
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Show personal confirmed visits with multi-month selection."""
    if not message.from_user:
        return

    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Откройте /my_visits в личном чате с ботом.")
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(_USER_NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return

    try:
        months = await attendance_service.get_user_visit_months(user.id)
        if not months:
            await message.answer(MY_VISITS_NO_VISITS)
            return

        if len(months) == 1:
            y, m = months[0]
            summary = await attendance_service.get_user_monthly_summary(user_id=user.id, year=y, month=m)
            visits = await attendance_service.get_user_monthly_visits(user_id=user.id, year=y, month=m)
            unique_import_ids = list(dict.fromkeys(v.import_id for v in visits if v.import_id is not None))
            text = format_my_visits_month_report(y, m, summary, visits)
            keyboard = get_my_visits_month_report_keyboard(unique_import_ids, has_multiple_months=False)
            await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            return

        text = MY_VISITS_CHOOSE_MONTH
        keyboard = get_my_visits_months_keyboard(months, page=0)
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")


@attendance_user_router.message(Command("my_rank"), flags={"rate_limit": "cheap"})
async def cmd_my_rank(
    message: Message,
    user_service: FromDishka[UserService],
    attendance_service: FromDishka[AttendanceService],
    settings: FromDishka[AppSettings],
) -> None:
    """Show user position in the monthly attendance leaderboard."""
    if not message.from_user:
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(_USER_NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return

    now = datetime.now(YEKATERINBURG_TZ)

    try:
        summary = await attendance_service.get_user_monthly_summary(
            user_id=user.id,
            year=now.year,
            month=now.month,
        )
        text = format_user_rank(summary, month=now.month, year=now.year)
        await message.answer(text, parse_mode=ParseMode.HTML)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")


@attendance_user_router.callback_query(AttendancePhotoCB.filter())
async def cb_view_attendance_photo(
    callback: CallbackQuery,
    callback_data: AttendancePhotoCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Send original attendance sheet photo if user is authorized."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    import_id = callback_data.import_id
    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    is_admin = False
    if user is not None:
        roles = await user_service.find_all_user_roles(user.id)
        if roles and "Admin" in roles:
            is_admin = True

    try:
        photo_bytes, mime_type, dates = await attendance_service.get_import_photo_with_access(
            import_id=import_id,
            requester_tg_id=callback.from_user.id,
            is_admin=is_admin,
        )

        dates_str = ", ".join(d.strftime("%d.%m.%Y") for d in dates) if dates else "дата не указана"
        caption = f"Импорт #{import_id} · {dates_str}"
        ext = "png" if "png" in mime_type.lower() else "jpg"
        photo_file = BufferedInputFile(photo_bytes, filename=f"attendance_import_{import_id}.{ext}")

        await callback.message.answer_photo(
            photo=photo_file,
            caption=caption,
        )
        await callback.answer()
    except AttendancePhotoAccessDeniedError as exc:
        await callback.answer(exc.message, show_alert=True)
    except AttendanceDomainError:
        await callback.answer("Фотография недоступна", show_alert=True)
    except Exception:
        logger.exception("Unexpected error while sending attendance photo")
        await callback.answer("Фотография недоступна", show_alert=True)


async def _render_month_report(  # noqa: PLR0913
    message: Message,
    user_id: int,
    year: int,
    month: int,
    has_multiple_months: bool,
    page: int,
    attendance_service: AttendanceService,
) -> None:
    """Render Screen 2 (month report) in the target message."""
    summary = await attendance_service.get_user_monthly_summary(user_id=user_id, year=year, month=month)
    visits = await attendance_service.get_user_monthly_visits(user_id=user_id, year=year, month=month)
    unique_import_ids = list(dict.fromkeys(v.import_id for v in visits if v.import_id is not None))
    text = format_my_visits_month_report(year, month, summary, visits)
    keyboard = get_my_visits_month_report_keyboard(
        unique_import_ids,
        has_multiple_months=has_multiple_months,
        page=page,
    )
    await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _render_months_selection(
    message: Message,
    months: list[tuple[int, int]],
    page: int,
) -> None:
    """Render Screen 1 (months selection) in the target message."""
    text = MY_VISITS_CHOOSE_MONTH
    keyboard = get_my_visits_months_keyboard(months, page=page)
    await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@attendance_user_router.callback_query(MyVisitsCB.filter(F.action == "month"))
async def cb_my_visits_select_month(
    callback: CallbackQuery,
    callback_data: MyVisitsCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Show Screen 2 (month report) for selected month."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    try:
        months = await attendance_service.get_user_visit_months(user.id)
        if not months:
            await callback.message.edit_text(MY_VISITS_NO_VISITS, reply_markup=None)
            await callback.answer()
            return

        target = (callback_data.year, callback_data.month)
        if target not in months:
            await _render_months_selection(callback.message, months, page=0)
        else:
            await _render_month_report(
                callback.message,
                user_id=user.id,
                year=callback_data.year,
                month=callback_data.month,
                has_multiple_months=len(months) > 1,
                page=callback_data.page,
                attendance_service=attendance_service,
            )
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Unexpected error in /my_visits month selection")
        await callback.answer("Ошибка при загрузке данных", show_alert=True)


@attendance_user_router.callback_query(MyVisitsCB.filter(F.action.in_({"months", "page"})))
async def cb_my_visits_to_months(
    callback: CallbackQuery,
    callback_data: MyVisitsCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Return to Screen 1 (months selection) or navigate pages."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    try:
        months = await attendance_service.get_user_visit_months(user.id)
        if not months:
            await callback.message.edit_text(MY_VISITS_NO_VISITS, reply_markup=None)
        elif len(months) == 1 and callback_data.action == "months":
            y, m = months[0]
            await _render_month_report(
                callback.message,
                user_id=user.id,
                year=y,
                month=m,
                has_multiple_months=False,
                page=0,
                attendance_service=attendance_service,
            )
        else:
            await _render_months_selection(callback.message, months, page=callback_data.page)

        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Unexpected error in /my_visits months navigation")
        await callback.answer("Ошибка при загрузке данных", show_alert=True)


cb_my_visits_navigation = cb_my_visits_select_month
