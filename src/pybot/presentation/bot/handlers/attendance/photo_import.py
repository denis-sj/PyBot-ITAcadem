"""Handlers for attendance photo sheet ingestion, review, and interactive editing."""

from __future__ import annotations

import html
import io

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....domain.exceptions import (
    AttendanceDomainError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
    AttendanceUserNotFoundError,
    UserNotFoundError,
)
from .....dto import AttendanceDraftDTO, AttendanceDraftItemDTO, parse_time_range
from .....services import UserService
from .....services.attendance_service import AttendanceService
from ....texts.attendance import format_draft_preview
from .keyboards import (
    AttendanceDraftCB,
    get_cancel_edit_keyboard,
    get_draft_actions_keyboard,
)
from .states import AttendanceEditSG

attendance_photo_router = Router(name="attendance_photo")


def _find_item_by_row_index(draft: AttendanceDraftDTO, target_index: int) -> AttendanceDraftItemDTO | None:
    """Find draft item by its 1-based visual row index in draft preview."""
    counter = 1
    for block in draft.dates:
        if block.is_ignored:
            continue
        for item in block.items:
            if counter == target_index:
                return item
            counter += 1
    return None


# ============ PHOTO UPLOAD HANDLER ============


@attendance_photo_router.message(
    F.chat.type == ChatType.PRIVATE,
    F.photo,
    flags={"role": "Admin", "rate_limit": "expensive"},
)
async def handle_attendance_photo(
    message: Message,
    bot: Bot,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Handle attendance sheet photo uploaded by Admin in private chat."""
    if not message.photo:
        return

    largest_photo = message.photo[-1]
    status_msg = await message.answer("🔄 <i>Загружаю и распознаю лист посещаемости...</i>")

    try:
        photo_buffer = io.BytesIO()
        await bot.download(largest_photo.file_id, destination=photo_buffer)
        photo_bytes = photo_buffer.getvalue()

        draft = await attendance_service.ingest_attendance_photo(
            author_tg_id=message.from_user.id if message.from_user else 0,
            photo_bytes=photo_bytes,
            mime_type="image/jpeg",
        )

        preview_text = format_draft_preview(draft)
        keyboard = get_draft_actions_keyboard(draft.draft_id)

        await status_msg.delete()
        await message.answer(preview_text, reply_markup=keyboard)

    except AttendancePhotoEmptyError:
        await status_msg.edit_text("⚠️ Файл фотографии пуст. Пожалуйста, отправьте корректное изображение.")
    except AttendanceRecognitionError as exc:
        await status_msg.edit_text(
            f"⚠️ <b>Ошибка распознавания листа:</b>\n{html.escape(exc.message)}\n\n"
            "Пожалуйста, сделайте более четкое фото при хорошем освещении и отправьте снова."
        )
    except AttendanceDomainError as exc:
        await status_msg.edit_text(f"⚠️ <b>Ошибка:</b> {html.escape(exc.message)}")
    except Exception:
        logger.exception("Unexpected error during attendance photo ingestion")
        await status_msg.edit_text("⚠️ Произошла непредвиденная ошибка при обработке фотографии.")


# ============ ACTION CALLBACKS ============


@attendance_photo_router.callback_query(
    AttendanceDraftCB.filter(F.action == "confirm"),
    flags={"role": "Admin"},
)
async def cb_confirm_draft(
    callback: CallbackQuery,
    callback_data: AttendanceDraftCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Confirm attendance draft and create records."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    admin_tg_id = callback.from_user.id
    try:
        added_dates, added_records, skipped = await attendance_service.confirm_draft(
            draft_id=callback_data.draft_id,
            admin_tg_id=admin_tg_id,
        )
        await callback.message.edit_text(
            f"✅ <b>Импорт успешно подтверждён и сохранён!</b>\n\n"
            f"• Добавлено дат: <b>{added_dates}</b>\n"
            f"• Добавлено записей: <b>{added_records}</b>\n"
            f"• Пропущено дат: <b>{skipped}</b>",
            reply_markup=None,
        )
        await callback.answer("Импорт успешно сохранён!")
    except AttendanceDomainError as exc:
        await callback.answer(f"Ошибка: {exc.message}", show_alert=True)
    except Exception:
        logger.exception("Failed to confirm attendance draft")
        await callback.answer("Ошибка при сохранении импорта", show_alert=True)


@attendance_photo_router.callback_query(
    AttendanceDraftCB.filter(F.action == "cancel"),
    flags={"role": "Admin"},
)
async def cb_cancel_draft(
    callback: CallbackQuery,
    callback_data: AttendanceDraftCB,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Cancel attendance draft and clear photo data."""
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer()
        return

    try:
        await attendance_service.cancel_draft(callback_data.draft_id)
        await callback.message.edit_text(
            "❌ <b>Импорт отменён.</b>\nЧерновик закрыт, а фото-доказательство удалено.",
            reply_markup=None,
        )
        await callback.answer("Импорт отменён")
    except AttendanceDomainError as exc:
        await callback.answer(f"Ошибка: {exc.message}", show_alert=True)


# ============ FSM EDIT: TIME ============


@attendance_photo_router.callback_query(
    AttendanceDraftCB.filter(F.action == "edittime"),
    flags={"role": "Admin"},
)
async def cb_start_edit_time(
    callback: CallbackQuery,
    callback_data: AttendanceDraftCB,
    state: FSMContext,
) -> None:
    """Prompt admin for row index to change time."""
    await state.set_state(AttendanceEditSG.waiting_for_row_selection_time)
    await state.update_data(draft_id=callback_data.draft_id)
    await callback.answer()
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer(
            "✏️ <b>Изменение времени</b>\n\nВведите номер строки (1, 2, ...), для которой нужно изменить время:",
            reply_markup=get_cancel_edit_keyboard(),
        )


@attendance_photo_router.message(
    AttendanceEditSG.waiting_for_row_selection_time,
    flags={"role": "Admin"},
)
async def msg_select_row_for_time(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Select row number for time editing."""
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("⚠️ Пожалуйста, введите корректный номер строки (число):")
        return

    row_index = int(text)
    data = await state.get_data()
    draft_id = data["draft_id"]

    try:
        draft = await attendance_service.get_draft(draft_id)
    except AttendanceDomainError:
        await state.clear()
        await message.answer("⚠️ Черновик не найден.")
        return

    item = _find_item_by_row_index(draft, row_index)
    if not item:
        await message.answer(f"⚠️ Строка с номером {row_index} не найдена. Попробуйте еще раз:")
        return

    await state.update_data(item_id=item.item_id)
    await state.set_state(AttendanceEditSG.waiting_for_new_time)

    cur_time = (
        f"{item.start_time.strftime('%H:%M')}–{item.end_time.strftime('%H:%M')}"
        if item.start_time and item.end_time
        else "не указано"
    )
    item_name = html.escape(item.matched_user_name or item.raw_name or "—")
    await message.answer(
        f"Выбрана строка {row_index} ({item_name}).\n"
        f"Текущее время: <b>{cur_time}</b>\n\n"
        "Введите новое время в формате <code>18:00-20:00</code>\n"
        "(или <code>-</code> / <code>нет</code>, если время не фиксируется):",
        reply_markup=get_cancel_edit_keyboard(),
    )


@attendance_photo_router.message(
    AttendanceEditSG.waiting_for_new_time,
    flags={"role": "Admin"},
)
async def msg_input_new_time(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Parse and apply new time to the selected row."""
    raw_time = (message.text or "").strip()
    is_empty_time = False
    start_time = None
    end_time = None

    if raw_time in ("-", "—", "–", "нет", "прочерк", "0"):
        is_empty_time = True
    else:
        parsed = parse_time_range(raw_time)
        if parsed is None:
            await message.answer(
                "⚠️ Некорректный формат времени. Введите время в формате <code>18:00-20:00</code> "
                "или <code>-</code> (для снятия фиксации времени):",
                reply_markup=get_cancel_edit_keyboard(),
            )
            return
        start_time, end_time, _ = parsed

    data = await state.get_data()
    draft_id = data["draft_id"]
    item_id = data["item_id"]

    try:
        updated_draft = await attendance_service.update_draft_item_time(
            draft_id=draft_id,
            item_id=item_id,
            start_time=start_time,
            end_time=end_time,
            is_empty_time=is_empty_time,
        )
        await state.clear()
        preview = format_draft_preview(updated_draft)
        keyboard = get_draft_actions_keyboard(draft_id)
        await message.answer(f"✅ Время обновлено!\n\n{preview}", reply_markup=keyboard)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ Ошибка: {exc.message}")


# ============ FSM EDIT: PARTICIPANT ============


@attendance_photo_router.callback_query(
    AttendanceDraftCB.filter(F.action == "edituser"),
    flags={"role": "Admin"},
)
async def cb_start_edit_user(
    callback: CallbackQuery,
    callback_data: AttendanceDraftCB,
    state: FSMContext,
) -> None:
    """Prompt admin for row index to assign participant."""
    await state.set_state(AttendanceEditSG.waiting_for_row_selection_user)
    await state.update_data(draft_id=callback_data.draft_id)
    await callback.answer()
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer(
            "👤 <b>Назначение участника</b>\n\nВведите номер строки (1, 2, ...), для которой нужно выбрать участника:",
            reply_markup=get_cancel_edit_keyboard(),
        )


@attendance_photo_router.message(
    AttendanceEditSG.waiting_for_row_selection_user,
    flags={"role": "Admin"},
)
async def msg_select_row_for_user(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Select row number for participant assignment."""
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("⚠️ Пожалуйста, введите корректный номер строки (число):")
        return

    row_index = int(text)
    data = await state.get_data()
    draft_id = data["draft_id"]

    try:
        draft = await attendance_service.get_draft(draft_id)
    except AttendanceDomainError:
        await state.clear()
        await message.answer("⚠️ Черновик не найден.")
        return

    item = _find_item_by_row_index(draft, row_index)
    if not item:
        await message.answer(f"⚠️ Строка с номером {row_index} не найдена. Попробуйте еще раз:")
        return

    await state.update_data(item_id=item.item_id)
    await state.set_state(AttendanceEditSG.waiting_for_user_selection)

    cur_name = item.matched_user_name or item.raw_name or "не распознано"
    await message.answer(
        f"Выбрана строка {row_index} (текущий: <i>{html.escape(cur_name)}</i>).\n\n"
        "Введите <b>ID пользователя</b> (или номер телефона):",
        reply_markup=get_cancel_edit_keyboard(),
    )


@attendance_photo_router.message(
    AttendanceEditSG.waiting_for_user_selection,
    flags={"role": "Admin"},
)
async def msg_input_user_target(
    message: Message,
    state: FSMContext,
    user_service: FromDishka[UserService],
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Resolve participant user and bind to row."""
    text = (message.text or "").strip()
    target_user = None

    if text.isdigit():
        target_id = int(text)
        try:
            target_user = await user_service.get_user(target_id)
        except UserNotFoundError:
            target_user = await user_service.find_user_by_telegram_id(target_id)
    else:
        target_user = await user_service.find_user_by_phone(text)
    if target_user is None:
        await message.answer(
            "⚠️ Участник не найден. Пожалуйста, проверьте ID или телефон и попробуйте снова:",
            reply_markup=get_cancel_edit_keyboard(),
        )
        return

    data = await state.get_data()
    draft_id = data["draft_id"]
    item_id = data["item_id"]

    try:
        updated_draft = await attendance_service.update_draft_item_participant(
            draft_id=draft_id,
            item_id=item_id,
            user_id=target_user.id,
        )
        await state.clear()
        preview = format_draft_preview(updated_draft)
        keyboard = get_draft_actions_keyboard(draft_id)
        await message.answer(
            f"✅ Участник назначен: <b>{html.escape(target_user.first_name)}</b>!\n\n{preview}",
            reply_markup=keyboard,
        )
    except (AttendanceDomainError, AttendanceUserNotFoundError) as exc:
        await message.answer(f"⚠️ Ошибка: {exc.message}")


# ============ FSM EDIT: EXCLUDE ============


@attendance_photo_router.callback_query(
    AttendanceDraftCB.filter(F.action == "exclude"),
    flags={"role": "Admin"},
)
async def cb_start_exclude(
    callback: CallbackQuery,
    callback_data: AttendanceDraftCB,
    state: FSMContext,
) -> None:
    """Prompt admin for row index to exclude."""
    await state.set_state(AttendanceEditSG.waiting_for_row_selection_exclude)
    await state.update_data(draft_id=callback_data.draft_id)
    await callback.answer()
    if callback.message and isinstance(callback.message, Message):
        await callback.message.answer(
            "🗑 <b>Исключение строки</b>\n\nВведите номер строки (1, 2, ...), которую нужно исключить из импорта:",
            reply_markup=get_cancel_edit_keyboard(),
        )


@attendance_photo_router.message(
    AttendanceEditSG.waiting_for_row_selection_exclude,
    flags={"role": "Admin"},
)
async def msg_select_row_to_exclude(
    message: Message,
    state: FSMContext,
    attendance_service: FromDishka[AttendanceService],
) -> None:
    """Mark row as excluded."""
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("⚠️ Пожалуйста, введите корректный номер строки (число):")
        return

    row_index = int(text)
    data = await state.get_data()
    draft_id = data["draft_id"]

    try:
        draft = await attendance_service.get_draft(draft_id)
    except AttendanceDomainError:
        await state.clear()
        await message.answer("⚠️ Черновик не найден.")
        return

    item = _find_item_by_row_index(draft, row_index)
    if not item:
        await message.answer(f"⚠️ Строка с номером {row_index} не найдена. Попробуйте еще раз:")
        return

    try:
        updated_draft = await attendance_service.exclude_draft_item(draft_id, item.item_id)
        await state.clear()
        preview = format_draft_preview(updated_draft)
        keyboard = get_draft_actions_keyboard(draft_id)
        await message.answer(f"✅ Строка {row_index} исключена!\n\n{preview}", reply_markup=keyboard)
    except AttendanceDomainError as exc:
        await message.answer(f"⚠️ Ошибка: {exc.message}")


# ============ CANCEL FSM ACTION ============


@attendance_photo_router.callback_query(F.data == "att_fsm_cancel")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel ongoing FSM edit action."""
    await state.clear()
    await callback.answer("Редактирование отменено")
    if callback.message and isinstance(callback.message, Message):
        await callback.message.delete()
