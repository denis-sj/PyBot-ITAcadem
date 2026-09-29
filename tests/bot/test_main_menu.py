"""Unit tests for the inline main menu, the registration button and the broadcast scenario from the menu."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
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
    BROADCAST_ALREADY_RUNNING,
    BROADCAST_MENU_CANCELLED,
    BROADCAST_MENU_CONFIRM,
    BROADCAST_MENU_DRAFT_LOST,
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


def _build_fsm_context() -> FSMContext:
    key = StorageKey(bot_id=1, chat_id=TELEGRAM_ID, user_id=TELEGRAM_ID)
    return FSMContext(storage=MemoryStorage(), key=key)


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
        get_broadcast_confirm_keyboard(),
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


def test_broadcast_confirmation_offers_send_and_cancel() -> None:
    assert _button_actions(get_broadcast_confirm_keyboard()) == [MenuAction.BROADCAST_SEND, MenuAction.BROADCAST_CANCEL]
    assert _button_actions(get_broadcast_cancel_keyboard()) == [MenuAction.BROADCAST_CANCEL]


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


@dataclass(slots=True)
class StubNotificationFacade:
    notify_user: AsyncMock = field(default_factory=AsyncMock)


def _build_broadcast_service(
    result: BroadcastResult | None = None,
    error: Exception | None = None,
) -> BroadcastService:
    service = AsyncMock(spec=BroadcastService)
    service.broadcast_for_all.return_value = result or BroadcastResult(
        attempted=5,
        sent=3,
        failed_temporary=1,
        failed_permanent=1,
        skipped_invalid_user=0,
    )
    service.broadcast_for_all.side_effect = error
    return cast(BroadcastService, service)


async def _state_with_draft(text: str = "Всем привет") -> FSMContext:
    state = _build_fsm_context()
    await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
    await state.update_data({"broadcast_text": text})
    return state


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
async def test_broadcast_text_handler_saves_draft_and_asks_for_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)
    state = _build_fsm_context()
    await state.set_state(BroadcastMenuSG.waiting_for_text)

    await broadcast_menu.msg_receive_broadcast_text(_build_message(text="Всем привет"), state)

    assert await state.get_state() == BroadcastMenuSG.waiting_for_confirmation.state
    assert (await state.get_data())["broadcast_text"] == "Всем привет"
    reply_mock.assert_awaited_once_with(BROADCAST_MENU_CONFIRM, reply_markup=get_broadcast_confirm_keyboard())


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
    callback = _build_callback()
    state = await _state_with_draft("Всем привет")
    broadcast_service = _build_broadcast_service()
    notification_facade = StubNotificationFacade()

    await broadcast_menu.cb_menu_broadcast_send(
        callback,
        state,
        broadcast_service=broadcast_service,
        notification_facade=cast(NotificationFacade, notification_facade),
    )

    cast(AsyncMock, broadcast_service.broadcast_for_all).assert_awaited_once_with(
        BroadcastDTO(broadcast_message="Всем привет")
    )
    notification_facade.notify_user.assert_awaited_once()
    notify_dto = notification_facade.notify_user.await_args.args[0]
    assert notify_dto.recipient_id == TELEGRAM_ID
    assert notify_dto.kind is TaskScheduleKind.IMMEDIATE
    assert notify_dto.message == broadcast_result_summary(broadcast_service.broadcast_for_all.return_value)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_repeated_press_of_send_button_does_not_broadcast_twice() -> None:
    state = await _state_with_draft()
    broadcast_service = _build_broadcast_service()
    notification_facade = StubNotificationFacade()
    first_press = _build_callback()
    second_press = _build_callback()

    for callback in (first_press, second_press):
        await broadcast_menu.cb_menu_broadcast_send(
            callback,
            state,
            broadcast_service=broadcast_service,
            notification_facade=cast(NotificationFacade, notification_facade),
        )

    cast(AsyncMock, broadcast_service.broadcast_for_all).assert_awaited_once()
    second_press.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_LOST)


@pytest.mark.asyncio
async def test_send_button_without_draft_reports_lost_draft_and_keeps_foreign_state() -> None:
    callback = _build_callback()
    state = _build_fsm_context()
    await state.set_state("SomeOtherScenario:step")
    broadcast_service = _build_broadcast_service()

    await broadcast_menu.cb_menu_broadcast_send(
        callback,
        state,
        broadcast_service=broadcast_service,
        notification_facade=cast(NotificationFacade, StubNotificationFacade()),
    )

    cast(AsyncMock, broadcast_service.broadcast_for_all).assert_not_awaited()
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_DRAFT_LOST)
    assert await state.get_state() == "SomeOtherScenario:step"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_text"),
    [
        (UsersNotFoundError(), BROADCAST_NO_RECIPIENTS),
        (BroadcastAlreadyRunningError(), BROADCAST_ALREADY_RUNNING),
    ],
)
async def test_confirmed_broadcast_reports_domain_errors(error: Exception, expected_text: str) -> None:
    callback = _build_callback()
    notification_facade = StubNotificationFacade()

    await broadcast_menu.cb_menu_broadcast_send(
        callback,
        await _state_with_draft(),
        broadcast_service=_build_broadcast_service(error=error),
        notification_facade=cast(NotificationFacade, notification_facade),
    )

    callback.message.answer.assert_awaited_once_with(expected_text)
    notification_facade.notify_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_drops_draft_and_sends_nothing() -> None:
    callback = _build_callback()
    state = await _state_with_draft()

    await broadcast_menu.cb_menu_broadcast_cancel(callback, state)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_CANCELLED)
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stale_cancel_button_does_not_reset_another_scenario() -> None:
    callback = _build_callback()
    state = _build_fsm_context()
    await state.set_state("SomeOtherScenario:step")

    await broadcast_menu.cb_menu_broadcast_cancel(callback, state)

    assert await state.get_state() == "SomeOtherScenario:step"
    callback.message.answer.assert_awaited_once_with(BROADCAST_MENU_CANCELLED)
