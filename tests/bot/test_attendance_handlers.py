"""Unit tests for attendance presentation handlers."""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
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
    AttendanceTopDTO,
    AttendanceUserSummaryDTO,
    UserReadDTO,
)
from pybot.dto.value_objects import Points
from pybot.presentation.bot.handlers.attendance.admin_commands import (
    cb_attendance_photo_back_to_months,
    cb_attendance_photo_select_month,
    cb_generate_report,
    cmd_attendance_photo,
    cmd_current_month,
    cmd_report_menu,
)
from pybot.domain.exceptions import (
    AttendanceDomainError,
    AttendancePhotoAccessDeniedError,
    AttendancePhotoNotFoundError,
)
from pybot.presentation.bot.handlers.attendance.keyboards import (
    AttendanceDraftCB,
    AttendancePhotoBackCB,
    AttendancePhotoBrowseCB,
    AttendancePhotoCB,
    AttendanceReportCB,
    MyVisitsCB,
    VisitsHubCB,
    get_attendance_photo_button,
    get_report_keyboard,
    get_visits_home_keyboard,
    get_visits_month_top_keyboard,
)
from pybot.presentation.bot.handlers.attendance import user_commands
from pybot.presentation.bot.handlers.attendance.user_commands import (
    cb_back_from_attendance_photo,
    cb_my_visits_navigation,
    cb_my_visits_to_months,
    cb_view_attendance_photo,
    cb_visits_hub,
    cmd_visits,
)
from pybot.presentation.bot.handlers.attendance.states import EditVisitsSG
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


def _build_fsm_context(user_id: int = 12345) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


# ============ USER COMMANDS TESTS ============


def _build_summary(total_minutes: int = 180, rank: int = 1, visits_count: int = 2) -> AttendanceUserSummaryDTO:
    return AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван",
        total_minutes=total_minutes,
        rank=rank,
        total_participants=5,
        visits_count=visits_count,
    )


def _build_top(period: tuple[date, date] = (date(2026, 9, 28), date(2026, 10, 3))) -> AttendanceTopDTO:
    row = AttendanceMonthlyRankingItemDTO(
        rank=1,
        user_id=10,
        user_full_name="Иванов Иван Иванович",
        total_minutes=180,
        visits_count=2,
    )
    return AttendanceTopDTO(
        period_start=period[0],
        period_end=period[1],
        rows=[row],
        total_participants=1,
        current_user=row,
    )


def _build_visit_record(import_id: int | None = 5, visit_date: date = date(2026, 9, 25)) -> AttendanceRecordReadDTO:
    return AttendanceRecordReadDTO(
        id=1,
        import_id=import_id,
        user_id=10,
        user_full_name="Иванов Иван",
        date=visit_date,
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        source="photo_ai",
        confirmed_at=datetime(2026, 9, 25, 20, 0),
    )


def _build_registered_services() -> tuple[AsyncMock, AsyncMock]:
    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = _build_user_read_dto()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_user_monthly_summary.return_value = _build_summary()
    return user_service, attendance_service


def _button_texts(markup: object) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
async def test_cmd_visits_registered_user_shows_home_card(
    monkeypatch: pytest.MonkeyPatch,
    chat_type: str,
) -> None:
    msg = _build_message(text="/visits", chat_type=chat_type)
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    user_service, attendance_service = _build_registered_services()

    await cmd_visits(msg, user_service, attendance_service)

    answer_mock.assert_awaited_once()
    text = answer_mock.call_args[0][0]
    assert "Посещаемость" in text
    assert "3 ч" in text
    assert "Иванов Иван" in text
    assert answer_mock.call_args.kwargs["parse_mode"] == ParseMode.HTML
    assert _button_texts(answer_mock.call_args.kwargs["reply_markup"]) == [
        "🏆 Топ недели",
        "🏆 Топ месяца",
        "📅 Мои посещения",
        "✖ Закрыть",
    ]


@pytest.mark.asyncio
async def test_cmd_visits_card_buttons_carry_owner_and_fit_callback_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/visits", from_user_id=9876543210)
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    user_service, attendance_service = _build_registered_services()

    await cmd_visits(msg, user_service, attendance_service)

    markup = answer_mock.call_args.kwargs["reply_markup"]
    for row in markup.inline_keyboard:
        for button in row:
            assert button.callback_data.endswith(":9876543210") or ":close:9876543210" in button.callback_data
            assert len(button.callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_cmd_visits_without_visits_still_offers_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_monthly_summary.return_value = _build_summary(total_minutes=0, rank=0, visits_count=0)

    await cmd_visits(msg, user_service, attendance_service)

    assert "у вас пока нет подтверждённых посещений" in answer_mock.call_args[0][0]
    assert "📅 Мои посещения" in _button_texts(answer_mock.call_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_cmd_visits_unregistered_user(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="/visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = None
    attendance_service = AsyncMock(spec=AttendanceService)

    await cmd_visits(msg, user_service, attendance_service)

    assert "не зарегистрированы" in answer_mock.call_args[0][0]
    attendance_service.get_user_monthly_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_visits_domain_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="/visits")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_monthly_summary.side_effect = AttendanceDomainError("Нет данных")

    await cmd_visits(msg, user_service, attendance_service)

    assert answer_mock.call_args[0][0] == "⚠️ Нет данных"


def test_removed_attendance_commands_have_no_handlers() -> None:
    for name in ("cmd_my_hours", "cmd_my_visits", "cmd_my_rank"):
        assert not hasattr(user_commands, name)


@pytest.mark.asyncio
async def test_cb_visits_hub_week_top_replaces_card_and_offers_back(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_week_top.return_value = _build_top()

    await cb_visits_hub(callback, VisitsHubCB(action="week", owner=12345), attendance_service, user_service)

    attendance_service.get_week_top.assert_awaited_once_with(10)
    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.call_args[0][0]
    assert "Топ недели · 28 сентября – 3 октября" in text
    assert "1. Иванов Иван — 3 ч · 2 посещения" in text
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"]) == ["← Назад"]
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_visits_hub_month_top_defaults_to_current_month_with_neighbour_arrows() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_month_top.return_value = _build_top(period=(date(2026, 9, 1), date(2026, 9, 30)))
    attendance_service.find_adjacent_attendance_months.return_value = ((2026, 8), None)

    await cb_visits_hub(callback, VisitsHubCB(action="top_month", owner=12345), attendance_service, user_service)

    call = attendance_service.get_month_top.await_args
    assert call.args == (10,)
    assert (
        call.kwargs["year"],
        call.kwargs["month"],
    ) == attendance_service.find_adjacent_attendance_months.await_args.args
    assert "Топ месяца" in callback.message.edit_text.call_args[0][0]
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"]) == ["← Август 2026", "← Назад"]


@pytest.mark.asyncio
async def test_cb_visits_hub_month_top_opens_requested_past_month() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_month_top.return_value = _build_top(period=(date(2026, 8, 1), date(2026, 8, 31)))
    attendance_service.find_adjacent_attendance_months.return_value = ((2026, 6), (2026, 9))

    await cb_visits_hub(
        callback,
        VisitsHubCB(action="top_month", year=2026, month=8, owner=12345),
        attendance_service,
        user_service,
    )

    attendance_service.get_month_top.assert_awaited_once_with(10, year=2026, month=8)
    attendance_service.find_adjacent_attendance_months.assert_awaited_once_with(2026, 8)
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"]) == [
        "← Июнь 2026",
        "Сентябрь 2026 →",
        "← Назад",
    ]


@pytest.mark.asyncio
async def test_cb_visits_hub_month_top_ignores_invalid_month_in_callback() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_month_top.return_value = _build_top(period=(date(2026, 9, 1), date(2026, 9, 30)))
    attendance_service.find_adjacent_attendance_months.return_value = (None, None)

    await cb_visits_hub(
        callback,
        VisitsHubCB(action="top_month", year=2026, month=13, owner=12345),
        attendance_service,
        user_service,
    )

    month = attendance_service.get_month_top.await_args.kwargs["month"]
    assert 1 <= month <= 12


@pytest.mark.asyncio
async def test_cb_visits_hub_home_returns_to_main_card() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()

    await cb_visits_hub(callback, VisitsHubCB(action="home", owner=12345), attendance_service, user_service)

    assert "Посещаемость" in callback.message.edit_text.call_args[0][0]
    assert "🏆 Топ недели" in _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_cb_visits_hub_my_visits_without_visits_shows_empty_state_with_back() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_visit_months.return_value = []

    await cb_visits_hub(callback, VisitsHubCB(action="my", owner=12345), attendance_service, user_service)

    assert callback.message.edit_text.call_args[0][0] == "У вас пока нет подтверждённых посещений."
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"]) == ["← Назад"]


@pytest.mark.asyncio
async def test_cb_visits_hub_my_visits_single_month_opens_it_with_photo_and_back() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_monthly_summary.return_value = _build_summary(total_minutes=120, visits_count=1)
    attendance_service.get_user_visit_months.return_value = [(2026, 9)]
    attendance_service.get_user_monthly_visits.return_value = [
        _build_visit_record(import_id=5),
        _build_visit_record(None),
    ]

    await cb_visits_hub(callback, VisitsHubCB(action="my", owner=12345), attendance_service, user_service)

    text = callback.message.edit_text.call_args[0][0]
    assert "Сентябрь 2026" in text
    assert "25.09 — 18:00–20:00 (2 ч) · Номер фото #5" in text
    assert "добавлено вручную" in text
    texts = _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"])
    assert texts == ["📷 Номер фото #5", "← Назад"]
    photo_button = callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert AttendancePhotoCB.unpack(photo_button.callback_data) == AttendancePhotoCB(import_id=5, owner=12345)


@pytest.mark.asyncio
async def test_visits_my_visits_multiple_months_flow_keeps_back_to_card() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_visit_months.return_value = [(2026, 9), (2026, 6)]
    attendance_service.get_user_monthly_visits.return_value = [_build_visit_record(import_id=10)]

    await cb_visits_hub(callback, VisitsHubCB(action="my", owner=12345), attendance_service, user_service)
    months_text = callback.message.edit_text.call_args[0][0]
    months_texts = _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"])
    assert "Ваши посещения" in months_text
    assert months_texts == ["Сентябрь 2026", "Июнь 2026", "← Назад"]

    callback.message.edit_text.reset_mock()
    await cb_my_visits_navigation(
        callback,
        MyVisitsCB(action="month", year=2026, month=9, page=0, owner=12345),
        attendance_service,
        user_service,
    )
    month_texts = _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"])
    assert month_texts == ["📷 Номер фото #10", "← К месяцам"]

    callback.message.edit_text.reset_mock()
    await cb_my_visits_to_months(
        callback,
        MyVisitsCB(action="months", page=0, owner=12345),
        attendance_service,
        user_service,
    )
    assert "Ваши посещения" in callback.message.edit_text.call_args[0][0]
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"])[-1] == "← Назад"


@pytest.mark.asyncio
async def test_cb_my_visits_month_without_any_visits_shows_empty_state_with_back() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_user_visit_months.return_value = []

    await cb_my_visits_navigation(
        callback,
        MyVisitsCB(action="month", year=2026, month=9, page=0, owner=12345),
        attendance_service,
        user_service,
    )

    assert callback.message.edit_text.call_args[0][0] == "У вас пока нет подтверждённых посещений."
    assert _button_texts(callback.message.edit_text.call_args.kwargs["reply_markup"]) == ["← Назад"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        VisitsHubCB(action="week", owner=12345),
        VisitsHubCB(action="top_month", owner=12345),
        VisitsHubCB(action="my", owner=12345),
        VisitsHubCB(action="home", owner=12345),
    ],
)
async def test_cb_visits_hub_rejects_press_from_other_user_in_group(callback_data: VisitsHubCB) -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999
    user_service, attendance_service = _build_registered_services()

    await cb_visits_hub(callback, callback_data, attendance_service, user_service)

    callback.message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs == {"show_alert": True}
    assert "другого участника" in callback.answer.call_args[0][0]
    attendance_service.get_week_top.assert_not_awaited()
    attendance_service.get_month_top.assert_not_awaited()
    attendance_service.get_user_visit_months.assert_not_awaited()


@pytest.mark.asyncio
async def test_my_visits_callbacks_reject_press_from_other_user() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999
    user_service, attendance_service = _build_registered_services()

    await cb_my_visits_navigation(
        callback,
        MyVisitsCB(action="month", year=2026, month=9, page=0, owner=12345),
        attendance_service,
        user_service,
    )
    await cb_my_visits_to_months(
        callback,
        MyVisitsCB(action="months", page=0, owner=12345),
        attendance_service,
        user_service,
    )

    callback.message.edit_text.assert_not_awaited()
    attendance_service.get_user_visit_months.assert_not_awaited()
    assert callback.answer.await_count == 2


@pytest.mark.asyncio
async def test_cb_view_attendance_photo_rejects_press_from_other_user() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999
    user_service, attendance_service = _build_registered_services()

    await cb_view_attendance_photo(
        callback, AttendancePhotoCB(import_id=5, owner=12345), attendance_service, user_service
    )

    callback.message.answer_photo.assert_not_awaited()
    attendance_service.get_import_photo_with_access.assert_not_awaited()
    assert callback.answer.call_args.kwargs == {"show_alert": True}


@pytest.mark.asyncio
async def test_cb_back_from_attendance_photo_rejects_press_from_other_user() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 999

    await cb_back_from_attendance_photo(callback, AttendancePhotoBackCB(owner=12345))

    callback.message.delete.assert_not_awaited()
    assert callback.answer.call_args.kwargs == {"show_alert": True}


@pytest.mark.asyncio
async def test_cb_visits_hub_ignores_message_is_not_modified() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    callback.message.edit_text.side_effect = TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: message is not modified"
    )
    user_service, attendance_service = _build_registered_services()

    await cb_visits_hub(callback, VisitsHubCB(action="home", owner=12345), attendance_service, user_service)

    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_visits_hub_unregistered_user_gets_alert() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service = AsyncMock(spec=UserService)
    user_service.find_user_by_telegram_id.return_value = None
    attendance_service = AsyncMock(spec=AttendanceService)

    await cb_visits_hub(callback, VisitsHubCB(action="home", owner=12345), attendance_service, user_service)

    callback.message.edit_text.assert_not_awaited()
    assert callback.answer.call_args.kwargs == {"show_alert": True}


@pytest.mark.asyncio
async def test_cb_visits_hub_domain_error_shows_alert() -> None:
    callback = _build_callback_mock()
    callback.from_user.id = 12345
    user_service, attendance_service = _build_registered_services()
    attendance_service.get_week_top.side_effect = AttendanceDomainError("Сбой рейтинга")

    await cb_visits_hub(callback, VisitsHubCB(action="week", owner=12345), attendance_service, user_service)

    callback.answer.assert_awaited_once_with("Сбой рейтинга", show_alert=True)


def test_visits_month_top_keyboard_without_neighbours_has_only_back() -> None:
    markup = get_visits_month_top_keyboard(owner_id=1, previous_month=None, next_month=None)

    assert _button_texts(markup) == ["← Назад"]


def test_visits_home_keyboard_close_button_is_restricted_to_owner() -> None:
    markup = get_visits_home_keyboard(owner_id=4242)

    close_button = markup.inline_keyboard[-1][0]
    assert close_button.callback_data == "nav:close:4242"


def test_get_attendance_photo_button() -> None:
    btn = get_attendance_photo_button(import_id=42)
    assert btn.text == "📷 Номер фото #42"
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
    assert call_kwargs["caption"] == "Номер фото #15 · 20.09.2026, 21.09.2026"
    assert call_kwargs["reply_markup"].inline_keyboard[0][0].text == "← Назад"
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
    state = _build_fsm_context()
    await state.set_state(EditVisitsSG.waiting_for_edit_time)
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    await cmd_current_month(msg, settings_obj, state)

    answer_mock.assert_awaited_once()
    msg_text = answer_mock.call_args[0][0]
    assert "Asia/Yekaterinburg" not in msg_text
    assert "Часовой пояс" not in msg_text
    assert "Текущий расчётный месяц учёта" in msg_text
    assert "Предыдущий ввод посещения отменён" in msg_text
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_cmd_report_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="/report")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    await cmd_report_menu(msg, _build_fsm_context())

    answer_mock.assert_awaited_once()
    assert "Учёт посещаемости" in answer_mock.call_args[0][0]
    reply_markup = answer_mock.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    assert not any(b.text == "✖ Закрыть" for b in all_buttons)


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


def test_get_report_keyboard_has_colored_action_buttons_without_close() -> None:
    kb = get_report_keyboard()
    all_buttons = [b for row in kb.inline_keyboard for b in row]
    assert not any(b.text == "✖ Закрыть" for b in all_buttons)
    assert next(button for button in all_buttons if "Добавить часы" in button.text).style == "success"


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
    await cmd_attendance_photo(msg, cmd_obj, attendance_service, _build_fsm_context())

    photo_mock.assert_awaited_once()
    assert photo_mock.call_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "← Назад"


@pytest.mark.asyncio
async def test_cmd_attendance_photo_without_arg_lists_recorded_months(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/attendance_photo")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_months.return_value = [(2026, 9)]
    cmd_obj = CommandObject(prefix="/", command="attendance_photo", args=None)

    await cmd_attendance_photo(msg, cmd_obj, attendance_service, _build_fsm_context())

    answer_mock.assert_awaited_once()
    assert "Выберите месяц" in answer_mock.call_args[0][0]
    buttons = [button for row in answer_mock.call_args.kwargs["reply_markup"].inline_keyboard for button in row]
    assert buttons[0].text == "Сентябрь 2026"


@pytest.mark.asyncio
async def test_cmd_attendance_photo_without_recorded_months_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _build_message(text="/attendance_photo")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_months.return_value = []
    cmd_obj = CommandObject(prefix="/", command="attendance_photo", args=None)

    await cmd_attendance_photo(msg, cmd_obj, attendance_service, _build_fsm_context())

    assert "Сохранённых фото посещаемости пока нет" in answer_mock.call_args[0][0]
    assert "reply_markup" not in answer_mock.call_args.kwargs


@pytest.mark.asyncio
async def test_cb_attendance_photo_select_month_shows_only_recorded_dates() -> None:
    callback = _build_callback_mock()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_dates_for_month.return_value = [
        (date(2026, 9, 25), 42),
        (date(2026, 9, 27), 45),
    ]

    await cb_attendance_photo_select_month(
        callback,
        AttendancePhotoBrowseCB(action="month", year=2026, month=9),
        attendance_service,
    )

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == [
        "25.09.2026 · Номер фото #42",
        "27.09.2026 · Номер фото #45",
        "← К месяцам",
    ]
    assert callback.answer.await_count == 1


@pytest.mark.asyncio
async def test_cb_attendance_photo_back_to_months_renders_month_keyboard() -> None:
    callback = _build_callback_mock()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_months.return_value = [(2026, 9)]

    await cb_attendance_photo_back_to_months(callback, attendance_service)

    assert "Выберите месяц" in callback.message.edit_text.call_args[0][0]
    buttons = [
        button for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert buttons[0].text == "Сентябрь 2026"


@pytest.mark.asyncio
async def test_cb_attendance_photo_select_month_keeps_back_button_when_no_dates() -> None:
    callback = _build_callback_mock()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_import_photo_dates_for_month.return_value = []

    await cb_attendance_photo_select_month(
        callback,
        AttendancePhotoBrowseCB(action="month", year=2026, month=9),
        attendance_service,
    )

    assert callback.message.edit_text.call_args[0][0] == "За этот месяц нет доступных фото."
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == ["← К месяцам"]


@pytest.mark.asyncio
async def test_cb_back_from_attendance_photo_deletes_photo_message() -> None:
    callback = _build_callback_mock()

    await cb_back_from_attendance_photo(callback, AttendancePhotoBackCB())

    callback.message.delete.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


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
    assert "Все даты на фото уже учтены (Номер фото #12). Сохранять нечего." in final_text
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
async def test_fsm_invalid_row_input_includes_cancel_button(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="not_a_number")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    state = AsyncMock()
    attendance_service = AsyncMock(spec=AttendanceService)

    await msg_select_row_for_time(msg, state, attendance_service)

    answer_mock.assert_awaited_once()
    assert answer_mock.call_args.kwargs.get("reply_markup") is not None
    reply_markup = answer_mock.call_args.kwargs["reply_markup"]
    assert any("Отмена" in btn.text for row in reply_markup.inline_keyboard for btn in row)


@pytest.mark.asyncio
async def test_fsm_invalid_time_input_includes_cancel_button(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_message(text="invalid_time")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    state = AsyncMock()
    attendance_service = AsyncMock(spec=AttendanceService)

    await msg_input_new_time(msg, state, attendance_service)

    answer_mock.assert_awaited_once()
    assert answer_mock.call_args.kwargs.get("reply_markup") is not None
    reply_markup = answer_mock.call_args.kwargs["reply_markup"]
    assert any("Отмена" in btn.text for row in reply_markup.inline_keyboard for btn in row)


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
