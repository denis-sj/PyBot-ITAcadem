"""Administrative commands for attendance reporting and photo evidence retrieval."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.config import YEKATERINBURG_TZ, AppSettings
from .....domain.exceptions import AttendanceDomainError
from .....services.attendance_service import AttendanceService
from ....texts.attendance import (
    MONTH_NAMES_RU_CAPITALIZED,
    format_monthly_report,
)
from ...keyboards.navigation import get_close_keyboard
from .keyboards import (
    AttendancePhotoBrowseCB,
    AttendanceReportCB,
    AttendanceUploadCB,
    get_attendance_photo_back_keyboard,
    get_attendance_photo_dates_keyboard,
    get_attendance_photo_months_keyboard,
    get_report_keyboard,
)
from .states import clear_attendance_fsm_state

attendance_admin_router = Router(name="attendance_admin")


@attendance_admin_router.message(
    Command("current_month"),
    flags={"role": "Admin", "rate_limit": "cheap"},
)
async def cmd_current_month(
    message: Message,
    settings: FromDishka[AppSettings],
    state: FSMContext,
) -> None:
    """Show information about current accounting month."""
    had_pending_attendance_input = await clear_attendance_fsm_state(state)
    now = datetime.now(YEKATERINBURG_TZ)
    month_name = MONTH_NAMES_RU_CAPITALIZED.get(now.month, f"Месяц {now.month}")
    cancelled_input_notice = "⚠️ Предыдущий ввод посещения отменён.\n\n" if had_pending_attendance_input else ""

    await message.answer(
        f"{cancelled_input_notice}📅 <b>Текущий расчётный месяц учёта:</b>\n\n"
        f"• Период: <b>{month_name} {now.year}</b>\n"
        f"• Номер месяца: <b>{now.month}</b>\n\n"
        f"<i>Все загруженные фото посещаемости за {now.month}.{now.year} учитываются в этом периоде.</i>",
        parse_mode=ParseMode.HTML,
    )


@attendance_admin_router.message(
    Command("report"),
    flags={"role": "Admin", "rate_limit": "moderate"},
)
async def cmd_report_menu(message: Message, state: FSMContext) -> None:
    """Display attendance actions for reports and photo imports."""
    await clear_attendance_fsm_state(state)
    await message.answer(
        "📊 <b>Учёт посещаемости</b>\nВыберите действие:",
        reply_markup=get_report_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@attendance_admin_router.callback_query(
    AttendanceUploadCB.filter(F.action == "photo"),
    flags={"role": "Admin"},
)
async def cb_start_attendance_photo_upload(callback: CallbackQuery) -> None:
    """Explain how to upload an attendance sheet from the report menu."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Загрузка доступна только в личном чате с ботом.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(
        "📸 <b>Добавление часов по фото</b>\n\n"
        "Отправьте фотографию листа посещаемости отдельным сообщением в этот чат. "
        "Выберите отправку как «Фото», а не как «Файл».\n\n"
        "После распознавания проверьте черновик и подтвердите импорт.",
        reply_markup=get_close_keyboard(),
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
    state: FSMContext,
) -> None:
    """Browse recorded photo dates or retrieve a photo by its number."""
    await clear_attendance_fsm_state(state)
    args = (command.args or "").strip()
    if args and not args.isdigit():
        await message.answer(
            "⚠️ Укажите номер фото или отправьте /attendance_photo, чтобы выбрать месяц и дату.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not args:
        try:
            months = await attendance_service.get_import_photo_months()
            if not months:
                await message.answer("📷 Сохранённых фото посещаемости пока нет.")
                return
            await message.answer(
                "📷 <b>Номера фото</b>\nВыберите месяц:",
                reply_markup=get_attendance_photo_months_keyboard(months),
                parse_mode=ParseMode.HTML,
            )
        except AttendanceDomainError as exc:
            await message.answer(f"⚠️ {exc.message}")
        except Exception:
            logger.exception("Failed to list attendance photo months")
            await message.answer("⚠️ Не удалось загрузить список фото.")
        return

    import_id = int(args)
    try:
        photo_bytes, mime_type = await attendance_service.get_photo_evidence(import_id)
        ext = ".png" if "png" in mime_type.lower() else ".jpg"
        file = BufferedInputFile(photo_bytes, filename=f"attendance_{import_id}{ext}")
        await message.answer_photo(
            photo=file,
            caption=f"📷 <b>Номер фото #{import_id}</b>",
            reply_markup=get_attendance_photo_back_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ {exc.message}")
    except Exception:
        logger.exception("Failed to fetch attendance photo evidence")
        await message.answer("⚠️ Не удалось открыть фото посещаемости.")


@attendance_admin_router.callback_query(
    AttendancePhotoBrowseCB.filter(F.action == "month"),
    flags={"role": "Admin"},
)
async def cb_attendance_photo_select_month(
    callback: CallbackQuery,
    callback_data: AttendancePhotoBrowseCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Show only recorded attendance dates for the selected month."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    month_name = MONTH_NAMES_RU_CAPITALIZED.get(callback_data.month, f"Месяц {callback_data.month}")
    try:
        dates = await attendance_service.get_import_photo_dates_for_month(
            year=callback_data.year,
            month=callback_data.month,
        )
        if not dates:
            await callback.message.edit_text(
                "За этот месяц нет доступных фото.",
                reply_markup=get_attendance_photo_dates_keyboard([]),
            )
        else:
            await callback.message.edit_text(
                f"📷 <b>Фото посещаемости · {month_name} {callback_data.year}</b>\nВыберите записанную дату:",
                reply_markup=get_attendance_photo_dates_keyboard(dates),
                parse_mode=ParseMode.HTML,
            )
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Failed to list attendance photos for month")
        await callback.answer("Не удалось загрузить даты", show_alert=True)


@attendance_admin_router.callback_query(
    AttendancePhotoBrowseCB.filter(F.action == "months"),
    flags={"role": "Admin"},
)
async def cb_attendance_photo_back_to_months(
    callback: CallbackQuery,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Return from recorded dates to the months that contain photos."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    try:
        months = await attendance_service.get_import_photo_months()
        if not months:
            await callback.message.edit_text("📷 Сохранённых фото посещаемости пока нет.")
        else:
            await callback.message.edit_text(
                "📷 <b>Номера фото</b>\nВыберите месяц:",
                reply_markup=get_attendance_photo_months_keyboard(months),
                parse_mode=ParseMode.HTML,
            )
        await callback.answer()
    except AttendanceDomainError as exc:
        await callback.answer(exc.message, show_alert=True)
    except Exception:
        logger.exception("Failed to reload attendance photo months")
        await callback.answer("Не удалось загрузить месяцы", show_alert=True)
