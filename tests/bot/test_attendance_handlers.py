"""Unit tests for attendance presentation handlers."""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.types import Chat, Message, PhotoSize, User

from pybot.core.config import AppSettings
from pybot.core.constants import PointsTypeEnum
from pybot.dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecordReadDTO,
    AttendanceUserSummaryDTO,
    UserReadDTO,
)
from pybot.dto.value_objects import Points
from pybot.presentation.bot.handlers.attendance.admin_commands import (
    cb_generate_report,
    cmd_attendance_photo,
    cmd_current_month,
    cmd_report_menu,
)
from pybot.domain.exceptions import AttendancePhotoAccessDeniedError, AttendancePhotoNotFoundError
from pybot.presentation.bot.handlers.attendance.keyboards import (
    AttendanceDraftCB,
    AttendancePhotoCB,
    AttendanceReportCB,
    MyVisitsCB,
    get_attendance_photo_button,
    get_report_keyboard,
)
from pybot.presentation.bot.handlers.attendance.user_commands import (
    cb_my_visits_navigation,
    cb_my_visits_to_months,
    cb_view_attendance_photo,
    cmd_my_hours,
    cmd_my_rank,
    cmd_my_visits,
)
from pybot.presentation.bot.handlers.attendance.photo_import import (
    cb_cancel_draft,
    cb_cancel_fsm,
    cb_confirm_draft,
    cb_start_edit_time,
    cb_start_edit_user,
    cb_start_exclude,
    handle_attendance_photo,
    msg_input_new_time,
    msg_input_user_target,
    msg_select_row_for_time,
    msg_select_row_for_user,
    msg_select_row_to_exclude,
)
from pybot.presentation.bot.handlers.attendance.user_commands import (
    cmd_my_hours,
    cmd_my_rank,
    cmd_my_visits,
)
from pybot.services import UserService
from pybot.services.attendance_service import AttendanceService


def _build_message(
    *,
    text: str = "",
    from_user_id: int = 12345,
    chat_type: str = "private",
    photo: list[PhotoSize] | None = None,
) -> Message:
    sender = User(id=from_user_id, is_bot=False, first_name="Тест")
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=from_user_id, type=chat_type),
        from_user=sender,
        text=text,
        photo=photo,
    )


def _build_user_read_dto(user_id: int = 10, tg_id: int = 12345) -> UserReadDTO:
    return UserReadDTO(
        id=user_id,
        telegram_id=tg_id,
        first_name="Иван",
        last_name="Иванов",
        patronymic=None,
        academic_points=Points(value=100, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=50, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2026, 1, 1),
    )


def _build_callback_mock() -> AsyncMock:
    cb = AsyncMock()
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    cb.message = msg
    cb.answer = AsyncMock()
    return cb


# ============ USER COMMANDS TESTS ============


@pytest.mark.asyncio
async def test_cmd_my_hours_registered_user(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/my_hours")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=180,
        rank=1,
        total_participants=5,
        visits_count=2,
    )

    await cmd_my_hours(msg, user_service, attendance_service, settings_obj)

    answer_mock.assert_awaited_once()
    assert "3 ч" in answer_mock.call_args[0][0]
    assert "#1" in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_hours_registered_user_no_visits(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/my_hours")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=0,
        rank=0,
        total_participants=5,
        visits_count=0,
    )

    await cmd_my_hours(msg, user_service, attendance_service, settings_obj)

    answer_mock.assert_awaited_once()
    assert "В этом месяце у вас пока нет посещений." in answer_mock.call_args[0][0]
    assert "Место в рейтинге" not in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_hours_unregistered_user(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/my_hours")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = None

    attendance_service = AsyncMock(spec=AttendanceService)

    await cmd_my_hours(msg, user_service, attendance_service, settings_obj)

    answer_mock.assert_awaited_once()
    assert "не зарегистрированы" in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_visits_group_chat_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/my_visits", chat_type="group")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    attendance_service = AsyncMock(spec=AttendanceService)

    await cmd_my_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    assert "Откройте /my_visits в личном чате с ботом." in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_visits_unregistered_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/my_visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = None
    attendance_service = AsyncMock(spec=AttendanceService)

    await cmd_my_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    assert "не зарегистрированы" in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_visits_no_visits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/my_visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_visit_months.return_value = []

    await cmd_my_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    assert "У вас пока нет подтверждённых посещений." in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_visits_single_month_renders_screen_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/my_visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_visit_months.return_value = [(2026, 9)]
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=120,
        rank=1,
        total_participants=3,
        visits_count=1,
    )
    attendance_service.get_user_monthly_visits.return_value = [
        AttendanceRecordReadDTO(
            id=1,
            import_id=5,
            user_id=10,
            user_full_name="Иванов Иван",
            date=date(2026, 9, 25),
            start_time=time(18, 0),
            end_time=time(20, 0),
            duration_minutes=120,
            is_empty_time=False,
            source="photo_ai",
            confirmed_at=datetime(2026, 9, 25, 20, 0),
        )
    ]

    await cmd_my_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    text = answer_mock.call_args[0][0]
    assert "Сентябрь 2026" in text
    assert "Посещений: 1 · Всего: 2 ч · Место: 1 из 3" in text
    assert "25.09 — 18:00–20:00 (2 ч) · импорт #5" in text

    reply_markup = answer_mock.call_args.kwargs["reply_markup"]
    buttons = [b for row in reply_markup.inline_keyboard for b in row]
    # Has photo button for import 5 and close button (no "to months")
    assert any(b.text == "📷 Фото импорта #5" for b in buttons)
    assert any(b.text == "✖ Закрыть" for b in buttons)
    assert not any(b.text == "← К месяцам" for b in buttons)


@pytest.mark.asyncio
async def test_cmd_my_visits_multiple_months_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/my_visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_visit_months.return_value = [(2026, 9), (2026, 6)]
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=180,
        rank=2,
        total_participants=5,
        visits_count=2,
    )
    attendance_service.get_user_monthly_visits.return_value = [
        AttendanceRecordReadDTO(
            id=1,
            import_id=10,
            user_id=10,
            user_full_name="Иванов Иван",
            date=date(2026, 9, 10),
            start_time=time(18, 0),
            end_time=time(21, 0),
            duration_minutes=180,
            is_empty_time=False,
            source="photo_ai",
            confirmed_at=datetime(2026, 9, 10, 20, 0),
        )
    ]

    # Initial command -> Screen 1 (months list)
    await cmd_my_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    screen1_text = answer_mock.call_args[0][0]
    assert "Ваши посещения" in screen1_text
    screen1_markup = answer_mock.call_args.kwargs["reply_markup"]
    screen1_buttons = [b for row in screen1_markup.inline_keyboard for b in row]
    assert any(b.text == "Сентябрь 2026" for b in screen1_buttons)
    assert any(b.text == "Июнь 2026" for b in screen1_buttons)
    assert any(b.text == "✖ Закрыть" for b in screen1_buttons)

    # Click month -> Screen 2
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    edit_mock = AsyncMock()
    callback.message.edit_text = edit_mock

    await cb_my_visits_navigation(
        callback,
        MyVisitsCB(action="month", year=2026, month=9, page=0),
        attendance_service,
        user_service,
    )

    edit_mock.assert_awaited_once()
    screen2_text = edit_mock.call_args[0][0]
    assert "Сентябрь 2026" in screen2_text
    screen2_markup = edit_mock.call_args.kwargs["reply_markup"]
    screen2_buttons = [b for row in screen2_markup.inline_keyboard for b in row]
    assert any(b.text == "📷 Фото импорта #10" for b in screen2_buttons)
    assert any(b.text == "← К месяцам" for b in screen2_buttons)

    # Click "← К месяцам" -> Screen 1
    edit_mock.reset_mock()
    await cb_my_visits_to_months(
        callback,
        MyVisitsCB(action="months", page=0),
        attendance_service,
        user_service,
    )
    edit_mock.assert_awaited_once()
    back_text = edit_mock.call_args[0][0]
    assert "Ваши посещения" in back_text


@pytest.mark.asyncio
async def test_cmd_my_rank_registered_user(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/my_rank")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=120,
        rank=3,
        total_participants=8,
        visits_count=1,
    )

    await cmd_my_rank(msg, user_service, attendance_service, settings_obj)

    answer_mock.assert_awaited_once()
    assert "#3" in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_my_rank_registered_user_no_visits(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/my_rank")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_monthly_summary.return_value = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=0,
        rank=0,
        total_participants=5,
        visits_count=0,
    )

    await cmd_my_rank(msg, user_service, attendance_service, settings_obj)

    answer_mock.assert_awaited_once()
    assert "В этом месяце у вас пока нет посещений." in answer_mock.call_args[0][0]
    assert "Место:" not in answer_mock.call_args[0][0]


def test_get_attendance_photo_button() -> None:
    btn = get_attendance_photo_button(import_id=42)
    assert btn.text == "📷 Фото импорта #42"
    assert btn.callback_data is not None
    cb = AttendancePhotoCB.unpack(btn.callback_data)
    assert cb.import_id == 42


@pytest.mark.asyncio
async def test_cb_view_attendance_photo_success() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 123
    answer_photo_mock = AsyncMock()
    callback.message.answer_photo = answer_photo_mock

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    user_service.find_all_user_roles.return_value = {"Student"}

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_with_access.return_value = (
        b"photo_data",
        "image/jpeg",
        [date(2026, 9, 20), date(2026, 9, 21)],
    )

    await cb_view_attendance_photo(
        callback,
        AttendancePhotoCB(import_id=15),
        attendance_service,
        user_service,
    )

    answer_photo_mock.assert_awaited_once()
    call_kwargs = answer_photo_mock.call_args.kwargs
    assert call_kwargs["caption"] == "Импорт #15 · 20.09.2026, 21.09.2026"
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_view_attendance_photo_access_denied() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    user_service.find_all_user_roles.return_value = set()

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_with_access.side_effect = AttendancePhotoAccessDeniedError(
        "Фото недоступно: посещение удалено."
    )

    await cb_view_attendance_photo(
        callback,
        AttendancePhotoCB(import_id=15),
        attendance_service,
        user_service,
    )

    callback.answer.assert_awaited_once_with("Фото недоступно: посещение удалено.", show_alert=True)


@pytest.mark.asyncio
async def test_cb_view_attendance_photo_not_found() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 123

    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    user_service.find_all_user_roles.return_value = {"Admin"}

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_with_access.side_effect = AttendancePhotoNotFoundError(
        "Фотография импорта не найдена."
    )

    await cb_view_attendance_photo(
        callback,
        AttendancePhotoCB(import_id=15),
        attendance_service,
        user_service,
    )

    callback.answer.assert_awaited_once_with("Фотография недоступна", show_alert=True)


# ============ ADMIN COMMANDS TESTS ============


@pytest.mark.asyncio
async def test_cmd_current_month(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    msg = _build_message(text="/current_month")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    await cmd_current_month(msg, settings_obj)

    answer_mock.assert_awaited_once()
    assert "Asia/Yekaterinburg" in answer_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_report_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="/report")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    await cmd_report_menu(msg)

    answer_mock.assert_awaited_once()
    assert "Отчёты по посещаемости" in answer_mock.call_args[0][0]
    reply_markup = answer_mock.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    assert any(b.text == "✖ Закрыть" for b in all_buttons)


@pytest.mark.asyncio
async def test_cb_generate_report_sends_html(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _build_callback_mock()
    edit_text_mock = AsyncMock()
    monkeypatch.setattr(callback.message, "edit_text", edit_text_mock)

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_monthly_report.return_value = AttendanceMonthlyReportDTO(
        year=2026,
        month=9,
        total_club_minutes=0,
        active_participants_count=0,
        average_minutes_per_participant=0,
        rankings=[],
        records=[],
    )

    await cb_generate_report(
        callback,
        AttendanceReportCB(mode="short"),
        attendance_service,
    )

    edit_text_mock.assert_awaited_once()
    call = edit_text_mock.await_args
    assert call is not None
    assert call.kwargs["parse_mode"] == ParseMode.HTML


def test_get_report_keyboard_contains_close_button() -> None:
    kb = get_report_keyboard()
    all_buttons = [b for row in kb.inline_keyboard for b in row]
    assert any(b.text == "✖ Закрыть" for b in all_buttons)


@pytest.mark.asyncio
async def test_cmd_attendance_photo_valid_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/attendance_photo 42")
    photo_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer_photo", photo_mock)

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_photo_evidence.return_value = (b"image_bytes", "image/jpeg")

    cmd_obj = CommandObject(prefix="/", command="attendance_photo", args="42")
    await cmd_attendance_photo(msg, cmd_obj, attendance_service)

    photo_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_attendance_photo_missing_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/attendance_photo")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    attendance_service = AsyncMock(spec=AttendanceService)
    cmd_obj = CommandObject(prefix="/", command="attendance_photo", args=None)

    await cmd_attendance_photo(msg, cmd_obj, attendance_service)

    answer_mock.assert_awaited_once()
    assert "Укажите номер импорта" in answer_mock.call_args[0][0]


# ============ PHOTO INGESTION AND CALLBACK TESTS ============


def _create_test_draft(draft_id: int = 15) -> AttendanceDraftDTO:
    item = AttendanceDraftItemDTO(
        item_id="item_1",
        date=date(2026, 9, 25),
        raw_name="Иванов И.",
        matched_user_id=1,
        matched_user_name="Иванов Иван",
        start_time=time(18, 0),
        end_time=time(19, 0),
        duration_minutes=60,
        is_empty_time=False,
        confidence=0.9,
        status="recognized",
    )
    block = AttendanceDraftDateBlockDTO(date=date(2026, 9, 25), items=[item])
    return AttendanceDraftDTO(
        draft_id=draft_id,
        author_tg_id=12345,
        created_at=datetime.now(UTC),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[block],
        status="DRAFT",
    )


@pytest.mark.asyncio
async def test_handle_attendance_photo_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=100, height=100)]
    msg = _build_message(photo=photo)

    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=status_msg))

    bot = AsyncMock()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.ingest_attendance_photo.return_value = _create_test_draft(15)

    await handle_attendance_photo(msg, bot, attendance_service)

    bot.download.assert_awaited_once()
    attendance_service.ingest_attendance_photo.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_attendance_photo_all_dates_processed_shows_close_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=100, height=100)]
    msg = _build_message(photo=photo)

    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    answer_mock = AsyncMock(return_value=status_msg)
    monkeypatch.setattr(Message, "answer", answer_mock)

    bot = AsyncMock()
    attendance_service = AsyncMock(spec=AttendanceService)
    processed_draft = AttendanceDraftDTO(
        draft_id=77,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 20),
                is_ignored=True,
                ignore_reason="дата уже обработана",
                processed_import_id=12,
                items=[],
            )
        ],
        status="DRAFT",
    )
    attendance_service.ingest_attendance_photo.return_value = processed_draft

    await handle_attendance_photo(msg, bot, attendance_service)

    assert answer_mock.await_count == 2
    final_call_args = answer_mock.call_args_list[1]
    final_text = final_call_args[0][0]
    assert "Все даты на фото уже учтены (импорт #12). Сохранять нечего." in final_text
    keyboard = final_call_args[1].get("reply_markup") or final_call_args.kwargs.get("reply_markup")
    assert keyboard is not None
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].text == "✖ Закрыть"


@pytest.mark.asyncio
async def test_cb_confirm_draft() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999

    cb_data = AttendanceDraftCB(action="confirm", draft_id=15)
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.confirm_draft.return_value = (1, 5, 0)

    await cb_confirm_draft(callback, cb_data, attendance_service)

    attendance_service.confirm_draft.assert_awaited_once_with(draft_id=15, admin_tg_id=999)
    callback.message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_cancel_draft() -> None:
    callback = _build_callback_mock()

    cb_data = AttendanceDraftCB(action="cancel", draft_id=15)
    attendance_service = AsyncMock(spec=AttendanceService)

    await cb_cancel_draft(callback, cb_data, attendance_service)

    attendance_service.cancel_draft.assert_awaited_once_with(15)
    callback.message.edit_text.assert_awaited_once()


# ============ FSM EDITING FLOW TESTS ============


@pytest.mark.asyncio
async def test_fsm_time_editing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Start edit time
    cb = _build_callback_mock()
    state = AsyncMock()
    cb_data = AttendanceDraftCB(action="edittime", draft_id=15)

    await cb_start_edit_time(cb, cb_data, state)
    state.set_state.assert_awaited_once()
    state.update_data.assert_awaited_once_with(draft_id=15)

    # 2. Select row for time
    msg_row = _build_message(text="1")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state.get_data.return_value = {"draft_id": 15}
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_draft.return_value = _create_test_draft(15)

    await msg_select_row_for_time(msg_row, state, attendance_service)
    state.update_data.assert_awaited_with(item_id="item_1")

    # 3. Input new time
    msg_time = _build_message(text="18:00-20:30")
    state.get_data.return_value = {"draft_id": 15, "item_id": "item_1"}
    attendance_service.update_draft_item_time.return_value = _create_test_draft(15)

    await msg_input_new_time(msg_time, state, attendance_service)

    attendance_service.update_draft_item_time.assert_awaited_once_with(
        draft_id=15,
        item_id="item_1",
        start_time=time(18, 0),
        end_time=time(20, 30),
        is_empty_time=False,
    )
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_fsm_user_editing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Start edit user
    cb = _build_callback_mock()
    state = AsyncMock()
    cb_data = AttendanceDraftCB(action="edituser", draft_id=15)

    await cb_start_edit_user(cb, cb_data, state)
    state.set_state.assert_awaited_once()

    # 2. Select row for user
    msg_row = _build_message(text="1")
    monkeypatch.setattr(Message, "answer", AsyncMock())
    state.get_data.return_value = {"draft_id": 15}
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_draft.return_value = _create_test_draft(15)

    await msg_select_row_for_user(msg_row, state, attendance_service)
    state.update_data.assert_awaited_with(item_id="item_1")

    # 3. Input user ID
    msg_user = _build_message(text="77")
    state.get_data.return_value = {"draft_id": 15, "item_id": "item_1"}
    user_service = AsyncMock(spec=UserService)
    user_service.get_user.return_value = _build_user_read_dto(user_id=77)
    attendance_service.update_draft_item_participant.return_value = _create_test_draft(15)

    await msg_input_user_target(msg_user, state, user_service, attendance_service)

    attendance_service.update_draft_item_participant.assert_awaited_once_with(
        draft_id=15,
        item_id="item_1",
        user_id=77,
    )
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_fsm_exclude_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    cb = _build_callback_mock()
    state = AsyncMock()
    cb_data = AttendanceDraftCB(action="exclude", draft_id=15)

    await cb_start_exclude(cb, cb_data, state)
    state.set_state.assert_awaited_once()

    msg_row = _build_message(text="1")
    monkeypatch.setattr(Message, "answer", AsyncMock())
    state.get_data.return_value = {"draft_id": 15}
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_draft.return_value = _create_test_draft(15)
    attendance_service.exclude_draft_item.return_value = _create_test_draft(15)

    await msg_select_row_to_exclude(msg_row, state, attendance_service)

    attendance_service.exclude_draft_item.assert_awaited_once_with(15, "item_1")
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_cancel_fsm() -> None:
    cb = _build_callback_mock()
    state = AsyncMock()

    await cb_cancel_fsm(cb, state)

    state.clear.assert_awaited_once()
    cb.answer.assert_awaited_once()


def test_attendance_admin_handlers_have_admin_role_flags() -> None:
    from pybot.presentation.bot.handlers.attendance.admin_commands import attendance_admin_router
    from pybot.presentation.bot.handlers.attendance.photo_import import attendance_photo_router

    for handler in attendance_admin_router.message.handlers:
        assert handler.flags.get("role") == "Admin"

    for handler in attendance_admin_router.callback_query.handlers:
        assert handler.flags.get("role") == "Admin"

    photo_handler = attendance_photo_router.message.handlers[0]
    assert photo_handler.flags.get("role") == "Admin"

    for handler in attendance_photo_router.callback_query.handlers:
        if getattr(handler.callback, "__name__", "") != "cb_cancel_fsm":
            assert handler.flags.get("role") == "Admin"


@pytest.mark.asyncio
async def test_handle_attendance_photo_error_flows(monkeypatch: pytest.MonkeyPatch) -> None:
    from pybot.domain.exceptions import AttendanceRecognitionError

    photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=100, height=100)]
    msg = _build_message(photo=photo)

    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=status_msg))
    bot = AsyncMock()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.ingest_attendance_photo.side_effect = AttendanceRecognitionError("Смазано")

    await handle_attendance_photo(msg, bot, attendance_service)
    status_msg.edit_text.assert_awaited_once()
    assert "Ошибка распознавания" in status_msg.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_confirm_draft_domain_error() -> None:
    from pybot.domain.exceptions import AttendanceDomainError

    callback = _build_callback_mock()
    cb_data = AttendanceDraftCB(action="confirm", draft_id=15)
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.confirm_draft.side_effect = AttendanceDomainError("Уже подтвержден")

    await cb_confirm_draft(callback, cb_data, attendance_service)
    callback.answer.assert_awaited_once()
    assert "Уже подтвержден" in callback.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_confirm_draft_unresolved_rows_error() -> None:
    from pybot.domain.exceptions import AttendanceDomainError

    callback = _build_callback_mock()
    cb_data = AttendanceDraftCB(action="confirm", draft_id=15)
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.confirm_draft.side_effect = AttendanceDomainError(
        "Нельзя подтвердить импорт: в журнале есть неразрешённые строки. Сопоставьте участников или исключите эти строки перед подтверждением."
    )

    await cb_confirm_draft(callback, cb_data, attendance_service)
    callback.answer.assert_awaited_once()
    assert "неразрешённые строки" in callback.answer.call_args[0][0]
