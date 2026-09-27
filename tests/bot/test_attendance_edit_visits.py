"""Bot flow tests for /edit_visits administration screens 1 to 4."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser

from pybot.db.models import AttendanceRecord, User
from pybot.dto import AttendanceRecordReadDTO
from pybot.core.config import AppSettings
from pybot.db.models import AttendanceChangeHistory, AttendanceRecord, User
from pybot.dto import AttendanceRecordReadDTO
from pybot.presentation.bot.handlers.attendance.edit_visits import (
    cb_confirm_delete,
    cb_edit_visits_back_to_users,
    cb_edit_visits_open_month,
    cb_edit_visits_open_record,
    cb_edit_visits_open_user,
    cb_edit_visits_page,
    cb_prompt_delete,
    cb_save_manual,
    cb_save_time,
    cb_start_edit_time,
    cb_start_manual,
    cmd_edit_visits,
    msg_input_edit_time,
    msg_input_manual_date,
    msg_input_manual_time,
    msg_input_user_number,
)
from pybot.presentation.bot.handlers.attendance.keyboards import EditVisitsCB
from pybot.presentation.bot.handlers.attendance.states import EditVisitsSG
from pybot.services.attendance_service import AttendanceService


def _build_tg_message(text: str, chat_type: str = "private", from_user_id: int = 12345) -> Message:
    sender = TgUser(id=from_user_id, is_bot=False, first_name="Admin")
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=from_user_id, type=chat_type),
        from_user=sender,
        text=text,
    )


def _build_fsm_context(user_id: int = 12345) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


def _build_callback_mock(from_user_id: int = 12345) -> AsyncMock:
    cb = AsyncMock(spec=CallbackQuery)
    cb.from_user = TgUser(id=from_user_id, is_bot=False, first_name="Admin")
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    cb.message = msg
    cb.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_cmd_edit_visits_group_chat_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_tg_message(text="/edit_visits", chat_type="group")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)

    await cmd_edit_visits(msg, state, attendance_service)

    answer_mock.assert_awaited_once()
    assert "Команда доступна только в личном чате с ботом." in answer_mock.call_args[0][0]
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_cmd_edit_visits_private_chat_renders_screen_1(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_tg_message(text="/edit_visits", chat_type="private")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_sorted_participants.return_value = [
        User(id=1, first_name="Иван", last_name="Иванов", patronymic="Иванович"),
        User(id=2, first_name="Петр", last_name="Петров"),
    ]

    await cmd_edit_visits(msg, state, attendance_service)

    assert await state.get_state() == EditVisitsSG.waiting_for_user_number.state
    answer_mock.assert_awaited_once()
    text = answer_mock.call_args[0][0]
    assert "<blockquote expandable>" in text
    assert "1. Иванов Иван Иванович" in text
    assert "2. Петров Петр" in text
    assert "Введите номер участника." in text

    markup = answer_mock.call_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.text == "✖ Закрыть" for b in buttons)


@pytest.mark.asyncio
async def test_msg_input_user_number_non_digit(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_tg_message(text="не число")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state = _build_fsm_context()
    await state.set_state(EditVisitsSG.waiting_for_user_number)
    attendance_service = AsyncMock(spec=AttendanceService)

    await msg_input_user_number(msg, state, attendance_service)

    answer_mock.assert_awaited_once()
    assert "Введите номер участника числом." in answer_mock.call_args[0][0]
    assert await state.get_state() == EditVisitsSG.waiting_for_user_number.state


@pytest.mark.asyncio
async def test_msg_input_user_number_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_tg_message(text="999")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state = _build_fsm_context()
    await state.set_state(EditVisitsSG.waiting_for_user_number)
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_sorted_participants.return_value = [
        User(id=1, first_name="Иван", last_name="Иванов"),
    ]

    await msg_input_user_number(msg, state, attendance_service)

    answer_mock.assert_awaited_once()
    assert "Нет участника с номером 999. Введите число от 1 до 1." in answer_mock.call_args[0][0]
    assert await state.get_state() == EditVisitsSG.waiting_for_user_number.state


@pytest.mark.asyncio
async def test_msg_input_user_number_valid_transitions_to_screen_2(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _build_tg_message(text="1")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    state = _build_fsm_context()
    await state.set_state(EditVisitsSG.waiting_for_user_number)
    attendance_service = AsyncMock(spec=AttendanceService)
    target_user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    attendance_service.get_sorted_participants.return_value = [target_user]
    attendance_service.get_user_visit_months.return_value = [(2026, 9)]

    await msg_input_user_number(msg, state, attendance_service)

    # State cleared
    assert await state.get_state() is None

    # Screen 2 rendered
    answer_mock.assert_awaited_once()
    text = answer_mock.call_args[0][0]
    assert "Иванов Иван Иванович" in text
    assert "Выберите месяц или добавьте посещение:" in text

    markup = answer_mock.call_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.text == "Сентябрь 2026" for b in buttons)
    assert any(b.text == "➕ Добавить посещение" for b in buttons)
    assert any(b.text == "← К списку участников" for b in buttons)


@pytest.mark.asyncio
async def test_cb_edit_visits_back_to_users_restores_state() -> None:
    callback = _build_callback_mock()
    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service.get_sorted_participants.return_value = [
        User(id=1, first_name="Иван", last_name="Иванов"),
    ]

    await cb_edit_visits_back_to_users(callback, state, attendance_service)

    assert await state.get_state() == EditVisitsSG.waiting_for_user_number.state
    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.call_args[0][0]
    assert "<blockquote expandable>" in text
    assert "1. Иванов Иван" in text
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_edit_visits_open_user_and_open_month_and_record_flow() -> None:
    callback = _build_callback_mock()
    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)
    user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    attendance_service.get_user_by_id.return_value = user

    # Screen 2: open user
    attendance_service.get_user_visit_months.return_value = [(2026, 9)]
    await cb_edit_visits_open_user(
        callback,
        EditVisitsCB(action="user", user_id=10),
        state,
        attendance_service,
    )
    callback.message.edit_text.assert_awaited_once()
    assert "Иванов Иван Иванович" in callback.message.edit_text.call_args[0][0]

    # Screen 3: open month
    callback.message.edit_text.reset_mock()
    rec = AttendanceRecord(
        id=100,
        import_id=12,
        user_id=10,
        date=date(2026, 9, 25),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        user=user,
    )
    # Return as read DTO
    rec_dto = AttendanceRecordReadDTO(
        id=100,
        import_id=12,
        user_id=10,
        user_full_name="Иванов Иван",
        date=date(2026, 9, 25),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        source="photo_ai",
        confirmed_at=datetime(2026, 9, 25, 20, 0),
    )
    attendance_service.get_user_monthly_visits.return_value = [rec_dto]

    await cb_edit_visits_open_month(
        callback,
        EditVisitsCB(action="month", user_id=10, year=2026, month=9),
        state,
        attendance_service,
    )
    callback.message.edit_text.assert_awaited_once()
    s3_text = callback.message.edit_text.call_args[0][0]
    assert "Иванов И. И. · Сентябрь 2026" in s3_text
    s3_markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    s3_buttons = [b for row in s3_markup.inline_keyboard for b in row]
    assert any(b.text == "25.09 · 18:00–20:00" for b in s3_buttons)
    assert any(b.text == "← К месяцам" for b in s3_buttons)

    # Screen 4: open record
    callback.message.edit_text.reset_mock()
    attendance_service.get_attendance_record.return_value = rec

    await cb_edit_visits_open_record(
        callback,
        EditVisitsCB(action="record", user_id=10, year=2026, month=9, record_id=100),
        state,
        attendance_service,
    )
    callback.message.edit_text.assert_awaited_once()
    s4_text = callback.message.edit_text.call_args[0][0]
    assert "Иванов И. И. · 25.09.2026" in s4_text
    assert "Время: <b>18:00–20:00</b> (2 ч)" in s4_text
    assert "Источник: импорт #12" in s4_text

    s4_markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    s4_buttons = [b for row in s4_markup.inline_keyboard for b in row]
    assert any(b.text == "📷 Фото импорта #12" for b in s4_buttons)
    assert any(b.text == "🕒 Изменить время" for b in s4_buttons)
    assert any(b.text == "🗑 Удалить" for b in s4_buttons)
    assert any(b.text == "← К датам" for b in s4_buttons)


@pytest.mark.asyncio
async def test_edit_visit_time_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _build_callback_mock()
    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)

    user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    rec = AttendanceRecord(
        id=50,
        import_id=1,
        user_id=10,
        date=date(2026, 9, 20),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        user=user,
    )
    attendance_service.get_attendance_record.return_value = rec

    # 1. Click start_edit_time
    await cb_start_edit_time(
        callback,
        EditVisitsCB(action="start_edit_time", user_id=10, year=2026, month=9, record_id=50),
        state,
        attendance_service,
    )
    assert await state.get_state() == EditVisitsSG.waiting_for_edit_time.state
    callback.message.edit_text.assert_awaited_once()
    prompt_text = callback.message.edit_text.call_args[0][0]
    assert "Изменение времени посещения" in prompt_text
    assert "ЧЧ:ММ-ЧЧ:ММ" in prompt_text

    # 2. Input invalid time format
    msg = _build_tg_message(text="18:00")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    await msg_input_edit_time(msg, state, attendance_service)
    assert "Неверный формат. Пример: 12:24-15:32" in answer_mock.call_args[0][0]

    # 3. Input end <= start
    answer_mock.reset_mock()
    msg = _build_tg_message(text="18:00-17:00")
    await msg_input_edit_time(msg, state, attendance_service)
    assert "Время окончания должно быть позже начала." in answer_mock.call_args[0][0]

    # 4. Input valid time -> confirmation
    answer_mock.reset_mock()
    msg = _build_tg_message(text="18:30-21:00")
    await msg_input_edit_time(msg, state, attendance_service)
    confirm_text = answer_mock.call_args[0][0]
    assert "Было: 18:00–20:00 → Станет: 18:30–21:00" in confirm_text
    assert "Сохранить изменения?" in confirm_text

    # 5. Click save_time
    updated_rec = AttendanceRecord(
        id=50,
        import_id=1,
        user_id=10,
        date=date(2026, 9, 20),
        start_time=time(18, 30),
        end_time=time(21, 0),
        duration_minutes=150,
        user=user,
    )
    attendance_service.edit_visit_time.return_value = updated_rec
    attendance_service.get_record_change_history.return_value = []
    callback.message.edit_text.reset_mock()

    await cb_save_time(
        callback,
        EditVisitsCB(action="save_time", user_id=10, year=2026, month=9, record_id=50),
        state,
        attendance_service,
    )

    assert await state.get_state() is None
    attendance_service.edit_visit_time.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    s4_text = callback.message.edit_text.call_args[0][0]
    assert "Время: <b>18:30–21:00</b> (2 ч 30 мин)" in s4_text


@pytest.mark.asyncio
async def test_delete_visit_flow() -> None:
    callback = _build_callback_mock()
    attendance_service = AsyncMock(spec=AttendanceService)

    user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    rec = AttendanceRecord(
        id=50,
        import_id=1,
        user_id=10,
        date=date(2026, 9, 20),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        user=user,
    )
    attendance_service.get_attendance_record.return_value = rec
    attendance_service.get_user_by_id.return_value = user

    # 1. Prompt delete
    await cb_prompt_delete(
        callback,
        EditVisitsCB(action="prompt_delete", user_id=10, year=2026, month=9, record_id=50),
        attendance_service,
    )
    callback.message.edit_text.assert_awaited_once()
    prompt_text = callback.message.edit_text.call_args[0][0]
    assert "Удалить посещение 20.09.2026 (18:00–20:00)?" in prompt_text

    # 2. Confirm delete (visits remain in month -> returns to Screen 3)
    callback.message.edit_text.reset_mock()
    attendance_service.delete_visit.return_value = (10, date(2026, 9, 20), "18:00–20:00")
    remaining_rec = AttendanceRecordReadDTO(
        id=51,
        user_id=10,
        user_full_name="Иванов Иван",
        date=date(2026, 9, 21),
        start_time=time(18, 0),
        end_time=time(19, 0),
        duration_minutes=60,
        source="photo_ai",
        confirmed_at=datetime(2026, 9, 21, 20, 0),
    )
    attendance_service.get_user_monthly_visits.return_value = [remaining_rec]

    await cb_confirm_delete(
        callback,
        EditVisitsCB(action="confirm_delete", user_id=10, year=2026, month=9, record_id=50),
        attendance_service,
    )
    attendance_service.delete_visit.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any("21.09 · 18:00–19:00" in b.text for b in buttons)


@pytest.mark.asyncio
async def test_manual_add_flow(monkeypatch: pytest.MonkeyPatch, settings_obj: AppSettings) -> None:
    callback = _build_callback_mock()
    state = _build_fsm_context()
    attendance_service = AsyncMock(spec=AttendanceService)
    attendance_service._attendance_repo = AsyncMock()
    attendance_service._db = AsyncMock()

    user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    attendance_service.get_user_by_id.return_value = user

    # 1. Start manual
    await cb_start_manual(
        callback,
        EditVisitsCB(action="start_manual", user_id=10),
        state,
    )
    assert await state.get_state() == EditVisitsSG.waiting_for_manual_date.state
    callback.message.edit_text.assert_awaited_once()
    assert "Добавление посещения вручную" in callback.message.edit_text.call_args[0][0]

    # 2. Input invalid date
    msg = _build_tg_message(text="32.13.2026")
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    await msg_input_manual_date(msg, state, attendance_service, settings_obj)
    assert "Неверный формат даты. Пример: 25.09.2026" in answer_mock.call_args[0][0]

    # 3. Input future date
    answer_mock.reset_mock()
    msg = _build_tg_message(text="01.01.2099")
    await msg_input_manual_date(msg, state, attendance_service, settings_obj)
    assert "Нельзя добавить посещение в будущую дату." in answer_mock.call_args[0][0]

    # 4. Input valid date
    attendance_service._attendance_repo.has_user_visit_on_date.return_value = False
    answer_mock.reset_mock()
    msg = _build_tg_message(text="15.09.2026")
    await msg_input_manual_date(msg, state, attendance_service, settings_obj)
    assert await state.get_state() == EditVisitsSG.waiting_for_manual_time.state
    assert "Время посещения" in answer_mock.call_args[0][0]

    # 5. Input valid time -> confirmation
    answer_mock.reset_mock()
    msg = _build_tg_message(text="18:00-20:00")
    await msg_input_manual_time(msg, state)
    assert "Добавить посещение 15.09.2026: 18:00–20:00?" in answer_mock.call_args[0][0]

    # 6. Save manual visit
    created_rec = AttendanceRecord(
        id=99,
        import_id=None,
        user_id=10,
        date=date(2026, 9, 15),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        source="manual",
        user=user,
    )
    attendance_service.create_manual_visit.return_value = created_rec
    attendance_service.get_record_change_history.return_value = []
    callback.message.edit_text.reset_mock()

    await cb_save_manual(
        callback,
        EditVisitsCB(action="save_manual", user_id=10),
        state,
        attendance_service,
    )

    assert await state.get_state() is None
    attendance_service.create_manual_visit.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    s4_text = callback.message.edit_text.call_args[0][0]
    assert "15.09.2026" in s4_text
    assert "Источник: добавлено вручную" in s4_text


@pytest.mark.asyncio
async def test_screen_4_renders_history() -> None:
    callback = _build_callback_mock()
    attendance_service = AsyncMock(spec=AttendanceService)

    user = User(id=10, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    rec = AttendanceRecord(
        id=50,
        import_id=1,
        user_id=10,
        date=date(2026, 9, 20),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        user=user,
    )
    attendance_service.get_attendance_record.return_value = rec
    attendance_service.get_user_by_id.return_value = user
    history = [
        AttendanceChangeHistory(
            id=1,
            user_id=10,
            record_id=50,
            visit_date=date(2026, 9, 20),
            action="edit_time",
            old_value="18:00–19:30",
            new_value="18:00–20:00",
            admin_tg_id=999,
            admin_name="Петров",
            created_at=datetime(2026, 9, 20, 21, 30),
        ),
        AttendanceChangeHistory(
            id=2,
            user_id=10,
            record_id=50,
            visit_date=date(2026, 9, 20),
            action="manual_add",
            old_value=None,
            new_value="18:00–19:30",
            admin_tg_id=999,
            admin_name="Сидоров",
            created_at=datetime(2026, 9, 20, 20, 0),
        ),
    ]
    attendance_service.get_record_change_history.return_value = history

    await cb_edit_visits_open_record(
        callback,
        EditVisitsCB(action="record", user_id=10, year=2026, month=9, record_id=50),
        AsyncMock(),
        attendance_service,
    )

    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.call_args[0][0]
    assert "История изменений:" in text
    assert "Изменено 20.09.2026 21:30, админ Петров (было 18:00–19:30)" in text
    assert "Добавлено вручную 20.09.2026 20:00, админ Сидоров" in text
    callback.answer.assert_awaited_once_with()
