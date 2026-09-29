"""User-facing Telegram command /visits: personal attendance card, tops and visit history."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.config import YEKATERINBURG_TZ
from .....domain.exceptions import AttendanceDomainError, AttendancePhotoAccessDeniedError
from .....services import UserService
from .....services.attendance_service import AttendanceService
from ....texts import CARD_NOT_OWNER_ALERT
from ....texts.attendance import (
    MY_VISITS_CHOOSE_MONTH,
    MY_VISITS_NO_VISITS,
    format_month_top,
    format_my_visits_month_report,
    format_visits_home,
    format_week_top,
)
from .keyboards import (
    AttendancePhotoBackCB,
    AttendancePhotoCB,
    MyVisitsCB,
    VisitsHubCB,
    get_attendance_photo_back_keyboard,
    get_my_visits_month_report_keyboard,
    get_my_visits_months_keyboard,
    get_visits_back_keyboard,
    get_visits_home_keyboard,
    get_visits_month_top_keyboard,
)

attendance_user_router = Router(name="attendance_user")

_USER_NOT_REGISTERED_MSG = (
    "ℹ️ <b>Вы ещё не зарегистрированы в системе клуба.</b>\n\n"
    "Пожалуйста, отправьте команду /start, чтобы пройти короткую регистрацию."
)
_MONTHS_PER_YEAR = 12

type _Screen = tuple[str, InlineKeyboardMarkup]


@attendance_user_router.message(Command("visits"), flags={"rate_limit": "cheap"})
async def cmd_visits(
    message: Message,
    user_service: FromDishka[UserService],
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Open the personal attendance card in a private or group chat."""
    if not message.from_user:
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(_USER_NOT_REGISTERED_MSG, parse_mode=ParseMode.HTML)
        return

    try:
        text, keyboard = await _build_home_screen(user.id, message.from_user.id, attendance_service)
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")


@attendance_user_router.callback_query(VisitsHubCB.filter())
async def cb_visits_hub(
    callback: CallbackQuery,
    callback_data: VisitsHubCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Switch the /visits card between the main screen, tops and own visits inside the same message."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    if await _reject_foreign_press(callback, callback_data.owner):
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    try:
        text, keyboard = await _build_hub_screen(callback_data, user.id, callback.from_user.id, attendance_service)
        await _show_screen(callback.message, text, keyboard)
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Unexpected error in /visits navigation")
        await callback.answer("Ошибка при загрузке данных", show_alert=True)


@attendance_user_router.callback_query(MyVisitsCB.filter(F.action == "month"))
async def cb_my_visits_select_month(
    callback: CallbackQuery,
    callback_data: MyVisitsCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Show the month report of own visits for the selected month."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    if await _reject_foreign_press(callback, callback_data.owner):
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    owner_id = callback.from_user.id
    try:
        months = await attendance_service.get_user_visit_months(user.id)
        if not months:
            screen = (MY_VISITS_NO_VISITS, get_visits_back_keyboard(owner_id))
        elif (callback_data.year, callback_data.month) not in months:
            screen = (MY_VISITS_CHOOSE_MONTH, get_my_visits_months_keyboard(months, owner_id=owner_id, page=0))
        else:
            screen = await _build_month_report(
                user_id=user.id,
                owner_id=owner_id,
                year=callback_data.year,
                month=callback_data.month,
                has_multiple_months=len(months) > 1,
                page=callback_data.page,
                attendance_service=attendance_service,
            )
        await _show_screen(callback.message, *screen)
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Unexpected error in /visits month selection")
        await callback.answer("Ошибка при загрузке данных", show_alert=True)


@attendance_user_router.callback_query(MyVisitsCB.filter(F.action.in_({"months", "page"})))
async def cb_my_visits_to_months(
    callback: CallbackQuery,
    callback_data: MyVisitsCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Return to the list of months with own visits or turn its page."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    if await _reject_foreign_press(callback, callback_data.owner):
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    owner_id = callback.from_user.id
    try:
        months = await attendance_service.get_user_visit_months(user.id)
        if not months:
            screen = (MY_VISITS_NO_VISITS, get_visits_back_keyboard(owner_id))
        elif len(months) == 1 and callback_data.action == "months":
            year, month = months[0]
            screen = await _build_month_report(
                user_id=user.id,
                owner_id=owner_id,
                year=year,
                month=month,
                has_multiple_months=False,
                page=0,
                attendance_service=attendance_service,
            )
        else:
            screen = (
                MY_VISITS_CHOOSE_MONTH,
                get_my_visits_months_keyboard(months, owner_id=owner_id, page=callback_data.page),
            )
        await _show_screen(callback.message, *screen)
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Unexpected error in /visits months navigation")
        await callback.answer("Ошибка при загрузке данных", show_alert=True)


cb_my_visits_navigation = cb_my_visits_select_month


@attendance_user_router.callback_query(AttendancePhotoCB.filter())
async def cb_view_attendance_photo(
    callback: CallbackQuery,
    callback_data: AttendancePhotoCB,
    attendance_service: FromDishka[AttendanceService],
    user_service: FromDishka[UserService],
) -> None:
    """Send original attendance sheet photo to the same chat if user is authorized."""
    if not callback.message or not isinstance(callback.message, Message) or not callback.from_user:
        await callback.answer()
        return
    if await _reject_foreign_press(callback, callback_data.owner):
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
        caption = f"Номер фото #{import_id} · {dates_str}"
        ext = "png" if "png" in mime_type.lower() else "jpg"
        photo_file = BufferedInputFile(photo_bytes, filename=f"attendance_import_{import_id}.{ext}")

        await callback.message.answer_photo(
            photo=photo_file,
            caption=caption,
            reply_markup=get_attendance_photo_back_keyboard(owner_id=callback_data.owner),
        )
        await callback.answer()
    except AttendancePhotoAccessDeniedError as exc:
        await callback.answer(exc.message, show_alert=True)
    except AttendanceDomainError:
        await callback.answer("Фотография недоступна", show_alert=True)
    except Exception:
        logger.exception("Unexpected error while sending attendance photo")
        await callback.answer("Фотография недоступна", show_alert=True)


@attendance_user_router.callback_query(AttendancePhotoBackCB.filter())
async def cb_back_from_attendance_photo(callback: CallbackQuery, callback_data: AttendancePhotoBackCB) -> None:
    """Delete the just-sent photo to reveal the preceding selection or details screen."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return
    if await _reject_foreign_press(callback, callback_data.owner):
        return

    try:
        await callback.message.delete()
        await callback.answer()
    except TelegramBadRequest:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Не удалось вернуться к предыдущему экрану.", show_alert=True)


async def _build_hub_screen(
    callback_data: VisitsHubCB,
    user_id: int,
    owner_id: int,
    attendance_service: AttendanceService,
) -> _Screen:
    """Build the screen requested by a /visits card button."""
    if callback_data.action == "week":
        top = await attendance_service.get_week_top(user_id)
        return format_week_top(top, user_id), get_visits_back_keyboard(owner_id)
    if callback_data.action == "top_month":
        return await _build_month_top_screen(callback_data, user_id, owner_id, attendance_service)
    if callback_data.action == "my":
        return await _build_my_visits_screen(user_id, owner_id, attendance_service)
    return await _build_home_screen(user_id, owner_id, attendance_service)


async def _build_home_screen(user_id: int, owner_id: int, attendance_service: AttendanceService) -> _Screen:
    """Build the main card: current month hours, visits and place in the monthly ranking."""
    now = datetime.now(YEKATERINBURG_TZ)
    summary = await attendance_service.get_user_monthly_summary(user_id=user_id, year=now.year, month=now.month)
    return format_visits_home(summary, month=now.month, year=now.year), get_visits_home_keyboard(owner_id)


async def _build_month_top_screen(
    callback_data: VisitsHubCB,
    user_id: int,
    owner_id: int,
    attendance_service: AttendanceService,
) -> _Screen:
    """Build the top of a month (the current one by default) with arrows to neighbouring months."""
    now = datetime.now(YEKATERINBURG_TZ)
    is_valid_month = callback_data.year > 0 and 1 <= callback_data.month <= _MONTHS_PER_YEAR
    year, month = (callback_data.year, callback_data.month) if is_valid_month else (now.year, now.month)

    top = await attendance_service.get_month_top(user_id, year=year, month=month)
    previous_month, next_month = await attendance_service.find_adjacent_attendance_months(year, month)
    return format_month_top(top, user_id), get_visits_month_top_keyboard(owner_id, previous_month, next_month)


async def _build_my_visits_screen(user_id: int, owner_id: int, attendance_service: AttendanceService) -> _Screen:
    """Build the entry screen of own visits: the only month at once or a list of months."""
    months = await attendance_service.get_user_visit_months(user_id)
    if not months:
        return MY_VISITS_NO_VISITS, get_visits_back_keyboard(owner_id)
    if len(months) == 1:
        year, month = months[0]
        return await _build_month_report(
            user_id=user_id,
            owner_id=owner_id,
            year=year,
            month=month,
            has_multiple_months=False,
            page=0,
            attendance_service=attendance_service,
        )
    return MY_VISITS_CHOOSE_MONTH, get_my_visits_months_keyboard(months, owner_id=owner_id, page=0)


async def _build_month_report(  # noqa: PLR0913
    user_id: int,
    owner_id: int,
    year: int,
    month: int,
    has_multiple_months: bool,
    page: int,
    attendance_service: AttendanceService,
) -> _Screen:
    """Build the month report of own visits with photo buttons."""
    summary = await attendance_service.get_user_monthly_summary(user_id=user_id, year=year, month=month)
    visits = await attendance_service.get_user_monthly_visits(user_id=user_id, year=year, month=month)
    unique_import_ids = list(dict.fromkeys(v.import_id for v in visits if v.import_id is not None))
    keyboard = get_my_visits_month_report_keyboard(
        unique_import_ids,
        has_multiple_months=has_multiple_months,
        page=page,
        owner_id=owner_id,
    )
    return format_my_visits_month_report(year, month, summary, visits), keyboard


async def _show_screen(message: Message, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Replace the card content in place, ignoring a repeated press that changes nothing."""
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            raise


async def _reject_foreign_press(callback: CallbackQuery, owner_id: int) -> bool:
    """Alert and return True when the card belongs to another user; owner_id 0 means an unrestricted button."""
    if owner_id and callback.from_user and callback.from_user.id != owner_id:
        await callback.answer(CARD_NOT_OWNER_ALERT, show_alert=True)
        return True
    return False
