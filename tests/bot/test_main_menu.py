"""Unit tests for the inline main menu, the registration button and the broadcast scenario from the menu."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User
from aiogram_dialog import DialogManager
from aiogram_dialog.api.entities.modes import ShowMode, StartMode

from pybot.core.config import AppSettings
from pybot.core.constants import PointsTypeEnum, TaskScheduleKind
from pybot.domain.exceptions import BroadcastAlreadyRunningError, UsersNotFoundError
from pybot.dto import BroadcastDTO, BroadcastResult, UserReadDTO
from pybot.dto.leaderboard_dto import LeaderboardPeriod
from pybot.dto.value_objects import Points
from pybot.presentation.bot.dialogs.user_reg.states import CreateProfileSG
from pybot.presentation.bot.handlers.broadcast import broadcast_menu
from pybot.presentation.bot.handlers.broadcast.states import BroadcastMenuSG
from pybot.presentation.bot.handlers.common import start as start_module
from pybot.presentation.bot.handlers.points import leaderboard as leaderboard_module
from pybot.presentation.bot.handlers.profile import user_profile as profile_module
from pybot.presentation.bot.keyboards.main_menu import (
    MainMenuCB,
    MenuAction,
    get_broadcast_cancel_keyboard,
    get_broadcast_confirm_keyboard,
    get_main_menu_keyboard,
    get_open_bot_registration_keyboard,
    get_start_registration_keyboard,
)
from pybot.presentation.bot.utils.menu_access import can_use_broadcast_menu
from pybot.presentation.bot.utils.registration_prompt import answer_registration_required
from pybot.presentation.texts import (
    BROADCAST_MENU_ALREADY_RUNNING,
    BROADCAST_MENU_CANCELLED,
    BROADCAST_MENU_CONFIRM,
    BROADCAST_MENU_DRAFT_OUTDATED,
    BROADCAST_MENU_IN_PROGRESS,
    BROADCAST_MENU_INTERRUPTED,
    BROADCAST_MENU_PROMPT,
    BROADCAST_NO_RECIPIENTS,
    HELP_PRIVATE,
    HELP_PRIVATE_PUBLIC,
    LEADERBOARD_UNEXPECTED_ERROR,
    REGISTRATION_REQUIRED_MESSAGE,
    broadcast_result_summary,
)
from pybot.services import LeaderboardService
from pybot.services.broadcast import BroadcastService
from pybot.services.notification_facade import NotificationFacade
from pybot.services.user_services import UserProfileService, UserRolesService, UserService

TELEGRAM_ID = 810_001
BOT_USERNAME = "pybot_test_bot"


def _button_texts(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _button_actions(markup: InlineKeyboardMarkup) -> list[MenuAction]:
    return [
        MainMenuCB.unpack(button.callback_data).action
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


def _build_message(*, chat_type: str = "private", text: str | None = None) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=TELEGRAM_ID, type=chat_type),
        from_user=User(id=TELEGRAM_ID, is_bot=False, first_name="Тест"),
        text=text,
    )


def _build_callback() -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    message.chat = Chat(id=TELEGRAM_ID, type="private")
    callback = AsyncMock(spec=CallbackQuery)
    callback.message = message
    callback.from_user = User(id=TELEGRAM_ID, is_bot=False, first_name="Тест")
    callback.answer = AsyncMock()
    return callback


def _build_bot(username: str | None = BOT_USERNAME) -> Bot:
    bot = AsyncMock(spec=Bot)
    bot.me.return_value = User(id=1, is_bot=True, first_name="PyBot", username=username)
    return cast(Bot, bot)


def _build_settings(broadcast_allowed_roles: set[str] | None = None) -> AppSettings:
    roles = {"Admin"} if broadcast_allowed_roles is None else broadcast_allowed_roles
    return cast(AppSettings, SimpleNamespace(broadcast_allowed_roles=roles))


def _build_user() -> UserReadDTO:
    return UserReadDTO(
        id=15,
        first_name="Илья",
        last_name="Тестов",
        patronymic=None,
        telegram_id=TELEGRAM_ID,
        academic_points=Points(value=50, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=10, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2025, 1, 10),
    )


def _build_user_service(user: UserReadDTO | None) -> UserService:
    service = AsyncMock(spec=UserService)
    service.find_user_by_telegram_id.return_value = user
    return cast(UserService, service)


def _build_profile_service() -> UserProfileService:
    service = AsyncMock(spec=UserProfileService)
    service.build_profile_view.return_value = SimpleNamespace()
    return cast(UserProfileService, service)


def _build_fsm_context(storage: MemoryStorage | None = None, user_id: int = TELEGRAM_ID) -> FSMContext:
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage or MemoryStorage(), key=key)


def _bad_request(reason: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=EditMessageText(text="x"), message=f"Bad Request: {reason}")


# ============ KEYBOARDS ============


def test_main_menu_has_the_four_basic_buttons() -> None:
    menu = get_main_menu_keyboard()

    assert _button_texts(menu) == ["👤 Профиль", "📊 Посещаемость", "🏆 Лидерборд", "❓ Помощь"]
    assert _button_actions(menu) == [MenuAction.PROFILE, MenuAction.VISITS, MenuAction.LEADERBOARD, MenuAction.HELP]


def test_main_menu_adds_broadcast_button_on_its_own_row() -> None:
    menu = get_main_menu_keyboard(show_broadcast=True)

    assert _button_texts(menu)[-1] == "📢 Рассылка всем"
    assert [len(row) for row in menu.inline_keyboard] == [2, 2, 1]
    assert _button_actions(menu)[-1] is MenuAction.BROADCAST


def test_menu_callback_data_fits_telegram_limit() -> None:
    markups = [
        get_main_menu_keyboard(show_broadcast=True),
        get_start_registration_keyboard(),
        get_broadcast_confirm_keyboard("0123abcd"),
        get_broadcast_cancel_keyboard(),
    ]

    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data is not None
                assert len(button.callback_data.encode()) <= 64


def test_start_registration_keyboard_starts_registration_in_place() -> None:
    markup = get_start_registration_keyboard()

    assert _button_texts(markup) == ["🚀 Начать регистрацию"]
    assert _button_actions(markup) == [MenuAction.REGISTER]
    assert markup.inline_keyboard[0][0].url is None


def test_open_bot_registration_keyboard_links_to_bot_chat_with_start_payload() -> None:
    markup = get_open_bot_registration_keyboard(BOT_USERNAME)

    button = markup.inline_keyboard[0][0]
    assert button.text == "🚀 Начать регистрацию"
    assert button.url == f"https://t.me/{BOT_USERNAME}?start=register"
    assert button.callback_data is None


def test_broadcast_confirmation_offers_send_and_cancel_bound_to_the_draft() -> None:
    markup = get_broadcast_confirm_keyboard("0123abcd")

    assert _button_actions(markup) == [MenuAction.BROADCAST_SEND, MenuAction.BROADCAST_CANCEL]
    for row in markup.inline_keyboard:
        for button in row:
            assert MainMenuCB.unpack(button.callback_data).draft == "0123abcd"
    assert _button_actions(get_broadcast_cancel_keyboard()) == [MenuAction.BROADCAST_CANCEL]
    assert MainMenuCB.unpack(get_broadcast_cancel_keyboard().inline_keyboard[0][0].callback_data).draft == ""


# ============ ACCESS AND REGISTRATION PROMPT ============


@pytest.mark.parametrize(
    ("user_roles", "allowed_roles", "expected"),
    [
        ({"Admin"}, {"Admin"}, True),
        ({"Student", "Admin"}, {"Admin"}, True),
        ({"Student"}, {"Admin"}, False),
        (set(), {"Admin"}, False),
        (None, {"Admin"}, False),
        ({"Admin"}, set(), False),
    ],
)
def test_can_use_broadcast_menu(user_roles: set[str] | None, allowed_roles: set[str], expected: bool) -> None:
    assert can_use_broadcast_menu(user_roles, _build_settings(allowed_roles)) is expected


@pytest.mark.asyncio
async def test_registration_prompt_in_private_chat_starts_registration_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)
    bot = _build_bot()

    await answer_registration_required(_build_message(chat_type="private"), bot)

    answer_mock.assert_awaited_once_with(
        REGISTRATION_REQUIRED_MESSAGE,
        reply_markup=get_start_registration_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    cast(AsyncMock, bot.me).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_registration_prompt_in_group_opens_bot_chat(
    chat_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_mock = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer_mock)

    await answer_registration_required(_build_message(chat_type=chat_type), _build_bot(), text="нужна регистрация")

    answer_mock.assert_awaited_once_with(
        "нужна регистрация",
        reply_markup=get_open_bot_registration_keyboard(BOT_USERNAME),
        parse_mode=ParseMode.HTML,
    )


# ============ PROFILE, HELP, REGISTER, LEADERBOARD ============


@pytest.mark.asyncio
async def test_cb_menu_profile_refreshes_profile_in_place_with_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _build_callback()
    monkeypatch.setattr(profile_module, "render_profile_message", Mock(return_value="profile text"))

    await profile_module.cb_menu_profile(
        callback,
        user_service=_build_user_service(_build_user()),
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
        bot=_build_bot(),
        user_roles={"Admin"},
    )

    callback.message.edit_text.assert_awaited_once_with(
        "profile text",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(show_broadcast=True),
    )
    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_profile_ignores_repeated_press_without_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _build_callback()
    callback.message.edit_text.side_effect = _bad_request("message is not modified")
    monkeypatch.setattr(profile_module, "render_profile_message", Mock(return_value="profile text"))

    await profile_module.cb_menu_profile(
        callback,
        user_service=_build_user_service(_build_user()),
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
        bot=_build_bot(),
    )

    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_profile_sends_new_message_when_message_cannot_be_edited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = _build_callback()
    callback.message.edit_text.side_effect = _bad_request("message can't be edited")
    monkeypatch.setattr(profile_module, "render_profile_message", Mock(return_value="profile text"))

    await profile_module.cb_menu_profile(
        callback,
        user_service=_build_user_service(_build_user()),
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
        bot=_build_bot(),
    )

    callback.message.answer.assert_awaited_once_with(
        "profile text",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_profile_offers_registration_to_unregistered_user() -> None:
    callback = _build_callback()
    profile_service = _build_profile_service()

    await profile_module.cb_menu_profile(
        callback,
        user_service=_build_user_service(None),
        user_profile_service=profile_service,
        settings=_build_settings(),
        bot=_build_bot(),
    )

    callback.message.answer.assert_awaited_once_with(
        REGISTRATION_REQUIRED_MESSAGE,
        reply_markup=get_start_registration_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    cast(AsyncMock, profile_service.build_profile_view).assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_profile_ignores_inaccessible_message() -> None:
    callback = _build_callback()
    callback.message = None
    user_service = _build_user_service(_build_user())

    await profile_module.cb_menu_profile(
        callback,
        user_service=user_service,
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
        bot=_build_bot(),
    )

    cast(AsyncMock, user_service.find_user_by_telegram_id).assert_not_awaited()
    callback.answer.assert_awaited_once_with()


def _build_roles_service(roles: list[str]) -> UserRolesService:
    service = AsyncMock(spec=UserRolesService)
    service.find_user_roles.return_value = roles
    return cast(UserRolesService, service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "roles", "expected_text"),
    [
        (42, ["Admin"], HELP_PRIVATE),
        (42, ["Student"], HELP_PRIVATE_PUBLIC),
        (None, ["Admin"], HELP_PRIVATE_PUBLIC),
    ],
)
async def test_cb_menu_help_sends_the_same_text_as_help_command(
    user_id: int | None,
    roles: list[str],
    expected_text: str,
) -> None:
    callback = _build_callback()

    await start_module.cb_menu_help(callback, user_roles_service=_build_roles_service(roles), user_id=user_id)

    callback.message.answer.assert_awaited_once_with(expected_text)
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_register_starts_registration_dialog_in_new_message() -> None:
    callback = _build_callback()
    dialog_manager = AsyncMock(spec=DialogManager)

    await start_module.cb_menu_register(
        callback,
        dialog_manager=dialog_manager,
        user_service=_build_user_service(None),
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
    )

    dialog_manager.start.assert_awaited_once_with(
        CreateProfileSG.welcome,
        mode=StartMode.RESET_STACK,
        show_mode=ShowMode.SEND,
    )
    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_register_shows_profile_when_user_is_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = _build_callback()
    dialog_manager = AsyncMock(spec=DialogManager)
    monkeypatch.setattr(start_module, "render_profile_message", Mock(return_value="profile text"))

    await start_module.cb_menu_register(
        callback,
        dialog_manager=dialog_manager,
        user_service=_build_user_service(_build_user()),
        user_profile_service=_build_profile_service(),
        settings=_build_settings(),
        user_roles={"Student"},
    )

    dialog_manager.start.assert_not_awaited()
    callback.message.answer.assert_awaited_once_with(
        "profile text",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
    callback.answer.assert_awaited_once_with()


def _build_leaderboard_service(*, should_raise: bool = False) -> LeaderboardService:
    service = AsyncMock(spec=LeaderboardService)
    service.get_previous_calendar_week_period = Mock(
        return_value=LeaderboardPeriod(
            start=datetime(2026, 9, 21, tzinfo=UTC),
            end=datetime(2026, 9, 28, tzinfo=UTC),
        )
    )
    if should_raise:
        service.get_previous_calendar_week_leaderboard.side_effect = RuntimeError("boom")
    else:
        service.get_previous_calendar_week_leaderboard.return_value = []
    return cast(LeaderboardService, service)


@pytest.mark.asyncio
async def test_cb_menu_leaderboard_sends_the_same_text_as_leaderboard_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = _build_callback()
    monkeypatch.setattr(leaderboard_module, "render_leaderboard_message", Mock(return_value="leaderboard text"))

    await leaderboard_module.cb_menu_leaderboard(callback, leaderboard_service=_build_leaderboard_service())

    callback.message.answer.assert_awaited_once_with("leaderboard text", parse_mode="HTML")
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_leaderboard_reports_unexpected_error() -> None:
    callback = _build_callback()

    await leaderboard_module.cb_menu_leaderboard(
        callback, leaderboard_service=_build_leaderboard_service(should_raise=True)
    )

    callback.message.answer.assert_awaited_once_with(LEADERBOARD_UNEXPECTED_ERROR)
    callback.answer.assert_awaited_once_with()


# ============ BROADCAST FROM THE MENU ============

DRAFT_ID = "draft001"
NEWER_DRAFT_ID = "draft002"
OTHER_ADMIN_ID = TELEGRAM_ID + 1
TELEGRAM_ALERT_LIMIT = 200
SUMMARY = BroadcastResult(attempted=5, sent=3, failed_temporary=1, failed_permanent=1, skipped_invalid_user=0)


@pytest.fixture(autouse=True)
def _no_leftover_sending_claims() -> Iterator[None]:
    """The claims and locks live in module state: a test must neither inherit nor leave them."""
    broadcast_menu._sending_admins.clear()
    broadcast_menu._scenario_locks.clear()
    yield
    broadcast_menu._sending_admins.clear()
    broadcast_menu._scenario_locks.clear()


class _YieldingStorage(MemoryStorage):
    """In-memory storage that yields to the event loop on every call, as a network storage (Redis) does.

    Without it every FSM call finishes without a real suspension and parallel handlers never interleave.
    """

    async def get_state(self, key: StorageKey) -> str | None:
        await asyncio.sleep(0)
        return await super().get_state(key)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await asyncio.sleep(0)
        await super().set_state(key, state)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        await asyncio.sleep(0)
        return await super().get_data(key)

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await asyncio.sleep(0)
        await super().set_data(key, data)


class _LockedBroadcast:
    """Stands in for ``BroadcastService.broadcast_for_all``: one broadcast at a time, it ends on ``release``."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._is_running = False

    async def broadcast(self, broadcast_data: BroadcastDTO) -> BroadcastResult:
        if self._is_running:
            raise BroadcastAlreadyRunningError
        self._is_running = True
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        finally:
            self._is_running = False
        return SUMMARY


async def _let_other_tasks_run() -> None:
    for _ in range(200):
        await asyncio.sleep(0)


async def _run_racing(
    first: Callable[[], Awaitable[object]],
    second: Callable[[], Awaitable[object]],
    *,
    delay: int,
    broadcast: _LockedBroadcast | None = None,
) -> None:
    """Run two handlers as parallel tasks, the second one ``delay`` event loop turns after the first one.

    A sending started by them is kept in the service until both handlers had time to reach their conclusions.
    """

    async def delayed() -> None:
        for _ in range(delay):
            await asyncio.sleep(0)
        await second()

    tasks = [asyncio.create_task(first()), asyncio.create_task(delayed())]
    await _let_other_tasks_run()
    if broadcast is not None:
        broadcast.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)


@dataclass(slots=True)
class StubNotificationFacade:
    notify_user: AsyncMock = field(default_factory=AsyncMock)


def _build_broadcast_service(side_effect: object = None) -> BroadcastService:
    service = AsyncMock(spec=BroadcastService)
    service.broadcast_for_all.return_value = BroadcastResult(
        attempted=5,
        sent=3,
        failed_temporary=1,
        failed_permanent=1,
        skipped_invalid_user=0,
    )
    service.broadcast_for_all.side_effect = side_effect
    return cast(BroadcastService, service)


def _broadcast_mock(service: BroadcastService) -> AsyncMock:
    return cast(AsyncMock, service.broadcast_for_all)


def _press(action: MenuAction, draft: str = "") -> MainMenuCB:
    return MainMenuCB(action=action, draft=draft)


async def _state_with_draft(
    text: str = "Всем привет",
    draft_id: str = DRAFT_ID,
    storage: MemoryStorage | None = None,
) -> FSMContext:
    state = _build_fsm_context(storage)
    await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
    await state.update_data({"broadcast_text": text, "broadcast_draft_id": draft_id})
    return state


async def _press_send(
    state: FSMContext,
    broadcast_service: BroadcastService,
    notification_facade: StubNotificationFacade | None = None,
    draft: str = DRAFT_ID,
    callback: AsyncMock | None = None,
) -> AsyncMock:
    callback = callback or _build_callback()
    await broadcast_menu.cb_menu_broadcast_send(
        callback,
        _press(MenuAction.BROADCAST_SEND, draft),
        state,
        broadcast_service=broadcast_service,
        notification_facade=cast(NotificationFacade, notification_facade or StubNotificationFacade()),
    )
    return callback


@asynccontextmanager
async def _live_sending(state: FSMContext) -> AsyncIterator[_LockedBroadcast]:
    """Run a real confirm-button handler and keep it in the middle of the sending until the block is left."""
    broadcast = _LockedBroadcast()
    task = asyncio.create_task(_press_send(state, _build_broadcast_service(broadcast.broadcast)))
    try:
        await asyncio.wait_for(broadcast.entered.wait(), timeout=1)
        yield broadcast
    finally:
        broadcast.release.set()
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_cb_menu_broadcast_asks_for_text_and_waits_for_it() -> None:
    callback = _build_callback()
    state = _build_fsm_context()

    await broadcast_menu.cb_menu_broadcast(callback, state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state
    callback.message.answer.assert_awaited_once_with(
        BROADCAST_MENU_PROMPT,
        parse_mode="HTML",
        reply_markup=get_broadcast_cancel_keyboard(),
    )
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cb_menu_broadcast_does_not_restart_scenario_while_sending() -> None:
    state = await _state_with_draft()

    async with _live_sending(state):
        callback = _build_callback()
        await broadcast_menu.cb_menu_broadcast(callback, state)

        assert await state.get_state() == BroadcastMenuSG.sending.state
        assert (await state.get_data())["broadcast_text"] == "Всем привет"
        callback.message.answer.assert_not_awaited()
        callback.answer.assert_awaited_once_with(BROADCAST_MENU_IN_PROGRESS)


@pytest.mark.asyncio
async def test_broadcast_text_handler_saves_draft_and_asks_for_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)
    state = _build_fsm_context()
    await state.set_state(BroadcastMenuSG.waiting_for_text)

    await broadcast_menu.msg_receive_broadcast_text(_build_message(text="Всем привет"), state)

    data = await state.get_data()
    assert await state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state
    assert data["broadcast_text"] == "Всем привет"
    reply_mock.assert_awaited_once_with(
        BROADCAST_MENU_CONFIRM,
        reply_markup=get_broadcast_confirm_keyboard(data["broadcast_draft_id"]),
    )


@pytest.mark.asyncio
async def test_every_new_draft_gets_its_own_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Message, "reply", AsyncMock())
    draft_ids = []

    for text in ("первый", "второй"):
        state = _build_fsm_context()
        await state.set_state(BroadcastMenuSG.waiting_for_text)
        await broadcast_menu.msg_receive_broadcast_text(_build_message(text=text), state)
        draft_ids.append((await state.get_data())["broadcast_draft_id"])

    assert draft_ids[0] != draft_ids[1]
    assert all(len(draft_id) == 8 for draft_id in draft_ids)


@pytest.mark.asyncio
async def test_broadcast_text_handler_ignores_blank_text(monkeypatch: pytest.MonkeyPatch) -> None:
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)
    state = _build_fsm_context()
    await state.set_state(BroadcastMenuSG.waiting_for_text)

    await broadcast_menu.msg_receive_broadcast_text(_build_message(text="   "), state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state
    reply_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_broadcast_goes_to_all_users_and_reports_summary_to_initiator() -> None:
    state = await _state_with_draft("Всем привет")
    broadcast_service = _build_broadcast_service()
    notification_facade = StubNotificationFacade()

    callback = await _press_send(state, broadcast_service, notification_facade)

    _broadcast_mock(broadcast_service).assert_awaited_once_with(BroadcastDTO(broadcast_message="Всем привет"))
    notification_facade.notify_user.assert_awaited_once()
    notify_dto = notification_facade.notify_user.await_args.args[0]
    assert notify_dto.recipient_id == TELEGRAM_ID
    assert notify_dto.kind is TaskScheduleKind.IMMEDIATE
    assert notify_dto.message == broadcast_result_summary(_broadcast_mock(broadcast_service).return_value)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_stale_confirm_button_never_sends_a_newer_draft() -> None:
    state = await _state_with_draft("новый текст", draft_id=NEWER_DRAFT_ID)
    broadcast_service = _build_broadcast_service()

    callback = await _press_send(state, broadcast_service, draft=DRAFT_ID)

    _broadcast_mock(broadcast_service).assert_not_awaited()
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
    assert await state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state
    assert (await state.get_data())["broadcast_text"] == "новый текст"


@pytest.mark.asyncio
async def test_confirm_button_of_replaced_draft_does_not_send_after_scenario_restart() -> None:
    state = await _state_with_draft("старый текст", draft_id=DRAFT_ID)
    broadcast_service = _build_broadcast_service()
    await broadcast_menu.cb_menu_broadcast(_build_callback(), state)

    callback = await _press_send(state, broadcast_service, draft=DRAFT_ID)

    _broadcast_mock(broadcast_service).assert_not_awaited()
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
    assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state


@pytest.mark.asyncio
async def test_repeated_press_after_sending_does_not_broadcast_twice() -> None:
    state = await _state_with_draft()
    broadcast_service = _build_broadcast_service()

    await _press_send(state, broadcast_service)
    second_press = await _press_send(state, broadcast_service)

    _broadcast_mock(broadcast_service).assert_awaited_once()
    second_press.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)


@pytest.mark.asyncio
async def test_press_while_sending_is_answered_without_second_broadcast() -> None:
    state = await _state_with_draft()

    async with _live_sending(state) as broadcast:
        second_service = _build_broadcast_service()
        callback = await _press_send(state, second_service)

        _broadcast_mock(second_service).assert_not_awaited()
        callback.answer.assert_awaited_once_with(BROADCAST_MENU_IN_PROGRESS)
        callback.message.answer.assert_not_awaited()
        assert await state.get_state() == BroadcastMenuSG.sending.state
        assert broadcast.calls == 1


@pytest.mark.asyncio
async def test_send_button_without_draft_reports_outdated_and_keeps_foreign_state() -> None:
    state = _build_fsm_context()
    await state.set_state("SomeOtherScenario:step")
    broadcast_service = _build_broadcast_service()

    callback = await _press_send(state, broadcast_service)

    _broadcast_mock(broadcast_service).assert_not_awaited()
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
    assert await state.get_state() == "SomeOtherScenario:step"


@pytest.mark.asyncio
async def test_confirmed_broadcast_without_recipients_reports_it_and_drops_draft() -> None:
    state = await _state_with_draft()
    notification_facade = StubNotificationFacade()

    callback = await _press_send(state, _build_broadcast_service(UsersNotFoundError()), notification_facade)

    callback.message.answer.assert_awaited_once_with(BROADCAST_NO_RECIPIENTS)
    notification_facade.notify_user.assert_not_awaited()
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_broadcast_already_running_keeps_draft_and_offers_to_retry() -> None:
    state = await _state_with_draft("Всем привет")
    broadcast_service = _build_broadcast_service(BroadcastAlreadyRunningError())
    notification_facade = StubNotificationFacade()

    callback = await _press_send(state, broadcast_service, notification_facade)

    callback.message.answer.assert_awaited_once_with(
        BROADCAST_MENU_ALREADY_RUNNING,
        reply_markup=get_broadcast_confirm_keyboard(DRAFT_ID),
    )
    notification_facade.notify_user.assert_not_awaited()
    assert await state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state
    assert (await state.get_data())["broadcast_text"] == "Всем привет"
    assert (await state.get_data())["broadcast_draft_id"] == DRAFT_ID


@pytest.mark.asyncio
async def test_retry_after_already_running_sends_the_saved_text() -> None:
    state = await _state_with_draft("Всем привет")
    result = BroadcastResult(attempted=1, sent=1, failed_temporary=0, failed_permanent=0, skipped_invalid_user=0)
    broadcast_service = _build_broadcast_service([BroadcastAlreadyRunningError(), result])
    notification_facade = StubNotificationFacade()

    await _press_send(state, broadcast_service, notification_facade)
    await _press_send(state, broadcast_service, notification_facade)

    assert _broadcast_mock(broadcast_service).await_count == 2
    for call in _broadcast_mock(broadcast_service).await_args_list:
        assert call.args[0] == BroadcastDTO(broadcast_message="Всем привет")
    notification_facade.notify_user.assert_awaited_once()
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_unexpected_broadcast_error_drops_draft_and_is_not_swallowed() -> None:
    state = await _state_with_draft()

    with pytest.raises(RuntimeError, match="boom"):
        await _press_send(state, _build_broadcast_service(RuntimeError("boom")))

    assert await state.get_state() is None
    assert not broadcast_menu._sending_admins


@pytest.mark.asyncio
async def test_cancel_drops_draft_of_the_pressed_button_and_sends_nothing() -> None:
    callback = _build_callback()
    state = await _state_with_draft()

    await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL, DRAFT_ID), state)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_CANCELLED)
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stale_cancel_button_does_not_drop_a_newer_draft() -> None:
    callback = _build_callback()
    state = await _state_with_draft("новый текст", draft_id=NEWER_DRAFT_ID)

    await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL, DRAFT_ID), state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state
    assert (await state.get_data())["broadcast_text"] == "новый текст"
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)


@pytest.mark.asyncio
async def test_prompt_cancel_button_leaves_the_scenario() -> None:
    callback = _build_callback()
    state = _build_fsm_context()
    await state.set_state(BroadcastMenuSG.waiting_for_text)

    await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL), state)

    assert await state.get_state() is None
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_CANCELLED)


@pytest.mark.asyncio
async def test_stale_prompt_cancel_button_does_not_reset_another_scenario() -> None:
    callback = _build_callback()
    state = _build_fsm_context()
    await state.set_state("SomeOtherScenario:step")

    await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL), state)

    assert await state.get_state() == "SomeOtherScenario:step"
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)


@pytest.mark.asyncio
async def test_cancel_cannot_interrupt_broadcast_being_sent() -> None:
    state = await _state_with_draft()

    async with _live_sending(state):
        callback = _build_callback()
        await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL), state)

        assert await state.get_state() == BroadcastMenuSG.sending.state
        assert (await state.get_data())["broadcast_text"] == "Всем привет"
        callback.message.answer.assert_not_awaited()
        callback.answer.assert_awaited_once_with(BROADCAST_MENU_IN_PROGRESS)


# ============ BROADCAST: PARALLEL PRESSES, CANCELLATION, INTERRUPTED SENDING ============


@pytest.mark.asyncio
async def test_parallel_confirm_presses_send_the_draft_only_once() -> None:
    state = await _state_with_draft(storage=_YieldingStorage())
    broadcast = _LockedBroadcast()
    service = _build_broadcast_service(broadcast.broadcast)
    notification_facade = StubNotificationFacade()
    callbacks = [_build_callback(), _build_callback()]

    tasks = [
        asyncio.create_task(_press_send(state, service, notification_facade, callback=callback))
        for callback in callbacks
    ]
    await asyncio.wait_for(broadcast.entered.wait(), timeout=1)
    await _let_other_tasks_run()
    broadcast.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert broadcast.calls == 1
    assert _broadcast_mock(service).await_count == 1
    rejected = [cb for cb in callbacks if call(BROADCAST_MENU_IN_PROGRESS) in cb.answer.await_args_list]
    assert len(rejected) == 1
    notification_facade.notify_user.assert_awaited_once()
    # neither press was offered a retry that would send the same draft once more
    for cb in callbacks:
        assert all(BROADCAST_MENU_ALREADY_RUNNING not in c.args for c in cb.message.answer.await_args_list)
    assert await state.get_state() is None
    assert await state.get_data() == {}

    late_press = await _press_send(state, service, notification_facade)

    late_press.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
    assert broadcast.calls == 1


@pytest.mark.asyncio
async def test_many_parallel_presses_still_send_once() -> None:
    state = await _state_with_draft(storage=_YieldingStorage())
    broadcast = _LockedBroadcast()
    service = _build_broadcast_service(broadcast.broadcast)

    tasks = [asyncio.create_task(_press_send(state, service)) for _ in range(5)]
    await asyncio.wait_for(broadcast.entered.wait(), timeout=1)
    await _let_other_tasks_run()
    broadcast.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert broadcast.calls == 1
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_sending_of_one_administrator_does_not_block_another_one() -> None:
    state = await _state_with_draft()
    other_state = _build_fsm_context(MemoryStorage(), user_id=OTHER_ADMIN_ID)
    await other_state.set_state(BroadcastMenuSG.waiting_for_confirmation)
    await other_state.update_data({"broadcast_text": "текст второго", "broadcast_draft_id": NEWER_DRAFT_ID})
    other_callback = _build_callback()
    other_callback.from_user = User(id=OTHER_ADMIN_ID, is_bot=False, first_name="Второй")

    async with _live_sending(state):
        # the service lock, not the per-administrator claim, serializes different administrators
        service = _build_broadcast_service(BroadcastAlreadyRunningError())
        await _press_send(other_state, service, draft=NEWER_DRAFT_ID, callback=other_callback)

        _broadcast_mock(service).assert_awaited_once()
        other_callback.message.answer.assert_awaited_once_with(
            BROADCAST_MENU_ALREADY_RUNNING,
            reply_markup=get_broadcast_confirm_keyboard(NEWER_DRAFT_ID),
        )
        assert await other_state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state


@pytest.mark.asyncio
async def test_cancelled_sending_frees_the_scenario_and_warns_about_partial_delivery() -> None:
    state = await _state_with_draft()
    broadcast = _LockedBroadcast()
    callback = _build_callback()
    notification_facade = StubNotificationFacade()
    service = _build_broadcast_service(broadcast.broadcast)
    task = asyncio.create_task(_press_send(state, service, notification_facade, callback=callback))
    await asyncio.wait_for(broadcast.entered.wait(), timeout=1)
    assert await state.get_state() == BroadcastMenuSG.sending.state

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert not broadcast_menu._sending_admins
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_INTERRUPTED)
    notification_facade.notify_user.assert_not_awaited()

    next_press = _build_callback()
    await broadcast_menu.cb_menu_broadcast(next_press, state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state
    next_press.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_failed_warning_does_not_replace_the_cancellation_or_leave_the_state_locked() -> None:
    state = await _state_with_draft()
    broadcast = _LockedBroadcast()
    callback = _build_callback()
    callback.message.answer.side_effect = _bad_request("chat not found")
    task = asyncio.create_task(_press_send(state, _build_broadcast_service(broadcast.broadcast), callback=callback))
    await asyncio.wait_for(broadcast.entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert await state.get_state() is None
    assert not broadcast_menu._sending_admins


async def _orphaned_sending_state() -> FSMContext:
    """The state a Redis storage keeps after the bot died in the middle of a sending: nobody sends it anymore."""
    state = await _state_with_draft()
    await state.set_state(BroadcastMenuSG.sending)
    return state


@pytest.mark.asyncio
async def test_menu_button_drops_orphaned_sending_and_warns_then_works_again() -> None:
    state = await _orphaned_sending_state()
    callback = _build_callback()

    await broadcast_menu.cb_menu_broadcast(callback, state)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    callback.answer.assert_awaited_once_with(BROADCAST_MENU_INTERRUPTED, show_alert=True)
    callback.message.answer.assert_not_awaited()

    next_press = _build_callback()
    await broadcast_menu.cb_menu_broadcast(next_press, state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state


@pytest.mark.asyncio
async def test_confirm_button_does_not_send_over_orphaned_sending() -> None:
    state = await _orphaned_sending_state()
    service = _build_broadcast_service()

    callback = await _press_send(state, service)

    _broadcast_mock(service).assert_not_awaited()
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.answer.assert_awaited_once_with(BROADCAST_MENU_INTERRUPTED, show_alert=True)
    callback.message.answer.assert_not_awaited()
    assert await state.get_state() is None
    assert not broadcast_menu._sending_admins


@pytest.mark.asyncio
async def test_cancel_button_drops_orphaned_sending_and_warns() -> None:
    state = await _orphaned_sending_state()
    callback = _build_callback()

    await broadcast_menu.cb_menu_broadcast_cancel(callback, _press(MenuAction.BROADCAST_CANCEL), state)

    assert await state.get_state() is None
    callback.answer.assert_awaited_once_with(BROADCAST_MENU_INTERRUPTED, show_alert=True)


def test_broadcast_alert_texts_fit_telegram_alert_limit() -> None:
    assert len(BROADCAST_MENU_INTERRUPTED) <= TELEGRAM_ALERT_LIMIT
    assert len(BROADCAST_MENU_IN_PROGRESS) <= TELEGRAM_ALERT_LIMIT


# ============ BROADCAST: SCENARIO CHANGES RACING WITH THE CONFIRMATION ============

RACE_DELAYS = range(12)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_first", [True, False])
@pytest.mark.parametrize("delay", RACE_DELAYS)
async def test_cancel_racing_with_confirm_never_disagrees_with_what_was_sent(cancel_first: bool, delay: int) -> None:
    state = await _state_with_draft(storage=_YieldingStorage())
    broadcast = _LockedBroadcast()
    service = _build_broadcast_service(broadcast.broadcast)
    cancel_callback, send_callback = _build_callback(), _build_callback()

    async def cancel() -> None:
        await broadcast_menu.cb_menu_broadcast_cancel(
            cancel_callback, _press(MenuAction.BROADCAST_CANCEL, DRAFT_ID), state
        )

    async def send() -> None:
        await _press_send(state, service, draft=DRAFT_ID, callback=send_callback)

    first, second = (cancel, send) if cancel_first else (send, cancel)
    await _run_racing(first, second, delay=delay, broadcast=broadcast)

    was_cancelled = call(BROADCAST_MENU_CANCELLED) in cancel_callback.message.answer.await_args_list
    was_sent = broadcast.calls == 1
    # The administrator is never told «cancelled» about a draft that goes out, and never sends what was cancelled.
    assert was_cancelled != was_sent
    if was_sent:
        cancel_callback.answer.assert_awaited_once_with(BROADCAST_MENU_IN_PROGRESS)
    else:
        send_callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("menu_first", [True, False])
@pytest.mark.parametrize("delay", RACE_DELAYS)
async def test_menu_button_racing_with_confirm_never_replaces_the_scenario_of_a_sent_draft(
    menu_first: bool,
    delay: int,
) -> None:
    state = await _state_with_draft(storage=_YieldingStorage())
    broadcast = _LockedBroadcast()
    service = _build_broadcast_service(broadcast.broadcast)
    menu_callback, send_callback = _build_callback(), _build_callback()

    async def menu() -> None:
        await broadcast_menu.cb_menu_broadcast(menu_callback, state)

    async def send() -> None:
        await _press_send(state, service, draft=DRAFT_ID, callback=send_callback)

    first, second = (menu, send) if menu_first else (send, menu)
    await _run_racing(first, second, delay=delay, broadcast=broadcast)

    was_restarted = menu_callback.message.answer.await_count == 1
    was_sent = broadcast.calls == 1
    assert was_restarted != was_sent
    if was_sent:
        menu_callback.answer.assert_awaited_once_with(BROADCAST_MENU_IN_PROGRESS)
        assert await state.get_state() is None
    else:
        send_callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_OUTDATED)
        assert await state.get_state() == BroadcastMenuSG.waiting_for_text.state


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_first", [True, False])
@pytest.mark.parametrize("delay", RACE_DELAYS)
async def test_cancel_racing_with_draft_text_leaves_no_ghost_draft(
    monkeypatch: pytest.MonkeyPatch,
    cancel_first: bool,
    delay: int,
) -> None:
    monkeypatch.setattr(Message, "reply", AsyncMock())
    state = _build_fsm_context(_YieldingStorage())
    await state.set_state(BroadcastMenuSG.waiting_for_text)
    cancel_callback = _build_callback()

    async def cancel() -> None:
        await broadcast_menu.cb_menu_broadcast_cancel(cancel_callback, _press(MenuAction.BROADCAST_CANCEL), state)

    async def receive_text() -> None:
        await broadcast_menu.msg_receive_broadcast_text(_build_message(text="Всем привет"), state)

    first, second = (cancel, receive_text) if cancel_first else (receive_text, cancel)
    await _run_racing(first, second, delay=delay)

    cancel_callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_CANCELLED)
    # The scenario is left, so a text that arrived after the cancel must not bring a draft back to life.
    assert await state.get_state() is None
    assert await state.get_data() == {}
