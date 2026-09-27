"""Administrative commands for attendance reporting and photo evidence retrieval."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.config import AppSettings
from .....domain.exceptions import AttendanceDomainError
from .....services.attendance_service import AttendanceService
from ....texts.attendance import (
    MONTH_NAMES_RU_CAPITALIZED,
    format_monthly_report,
)
from .keyboards import AttendanceReportCB, get_report_keyboard

attendance_admin_router = Router(name="attendance_admin")


@attendance_admin_router.message(
    Command("current_month"),
    flags={"role": "Admin", "rate_limit": "cheap"},
)
async def cmd_current_month(
    message: Message,
    settings: FromDishka[AppSettings],
) -> None:
    """Show information about current accounting month."""
    tz = ZoneInfo(str(settings.attendance_timezone))
    now = datetime.now(tz)
    month_name = MONTH_NAMES_RU_CAPITALIZED.get(now.month, f"Месяц {now.month}")

    await message.answer(
        f"📅 <b>Текущий расчётный месяц учёта:</b>\n\n"
        f"• Период: <b>{month_name} {now.year}</b>\n"
        f"• Номер месяца: <b>{now.month}</b>\n\n"
        f"<i>Все загруженные фото посещаемости за {now.month}.{now.year} учитываются в этом периоде.</i>",
        parse_mode=ParseMode.HTML,
    )


@attendance_admin_router.message(
    Command("report"),
    flags={"role": "Admin", "rate_limit": "moderate"},
)
async def cmd_report_menu(message: Message) -> None:
    """Display menu for generating monthly attendance reports."""
    await message.answer(
        "📊 <b>Отчёты по посещаемости клуба</b>\n\nВыберите тип отчёта за текущий месяц:",
        reply_markup=get_report_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@attendance_admin_router.callback_query(
    AttendanceReportCB.filter(),
    flags={"role": "Admin"},
)
async def cb_generate_report(
    callback: CallbackQuery,
    callback_data: AttendanceReportCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Generate and display monthly attendance report."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    is_detailed = callback_data.mode == "detailed"
    await callback.answer("Формирую отчёт...")

    try:
        report = await attendance_service.get_monthly_report(detailed=is_detailed)
        text = format_monthly_report(report, detailed=is_detailed)
        await callback.message.edit_text(
            text,
            reply_markup=get_report_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except AttendanceDomainError as exc:
        await callback.message.edit_text(f"⚠️ Ошибка: {exc.message}")
    except Exception:
        logger.exception("Failed to generate monthly attendance report")
        await callback.message.answer("⚠️ Не удалось сформировать отчёт.")


@attendance_admin_router.message(
    Command("attendance_photo"),
    flags={"role": "Admin", "rate_limit": "expensive"},
)
async def cmd_attendance_photo(
    message: Message,
    command: CommandObject,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Retrieve and display the original photo for a confirmed import."""
    args = (command.args or "").strip()
    if not args or not args.isdigit():
        await message.answer(
            "⚠️ <b>Укажите номер импорта</b>\n\n"
            "Использование: <code>/attendance_photo &lt;import_id&gt;</code>\n"
            "Пример: <code>/attendance_photo 42</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    import_id = int(args)
    try:
        photo_bytes, mime_type = await attendance_service.get_photo_evidence(import_id)
        ext = ".png" if "png" in mime_type.lower() else ".jpg"
        file = BufferedInputFile(photo_bytes, filename=f"attendance_{import_id}{ext}")
        await message.answer_photo(
            photo=file,
            caption=f"📸 <b>Фото-доказательство к импорту #{import_id}</b>",
            parse_mode=ParseMode.HTML,
        )
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")
    except Exception:
        logger.exception("Failed to fetch attendance photo evidence")
        await message.answer("⚠️ Ошибка при получении фотографии импорта.")
