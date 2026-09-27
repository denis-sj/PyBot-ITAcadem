"""User-facing Telegram commands for personal attendance hours and statistics."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.config import AppSettings
from .....domain.exceptions import AttendanceDomainError, AttendancePhotoAccessDeniedError
from .....services import UserService
from .....services.attendance_service import AttendanceService
from ....texts.attendance import format_user_rank, format_user_summary, format_user_visits
from .keyboards import AttendancePhotoCB

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

    tz = ZoneInfo(str(settings.attendance_timezone))
    now = datetime.now(tz)

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
    settings: FromDishka[AppSettings],
) -> None:
    """Show chronological list of confirmed visits in the current month."""
    if not message.from_user:
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(_USER_NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return

    tz = ZoneInfo(str(settings.attendance_timezone))
    now = datetime.now(tz)

    try:
        visits = await attendance_service.get_user_monthly_visits(
            user_id=user.id,
            year=now.year,
            month=now.month,
        )
        text = format_user_visits(visits, month=now.month, year=now.year)
        await message.answer(text, parse_mode=ParseMode.HTML)
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

    tz = ZoneInfo(str(settings.attendance_timezone))
    now = datetime.now(tz)

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
