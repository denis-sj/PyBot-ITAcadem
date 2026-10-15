from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import pytest
from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, TelegramObject, Update, User
from aiogram_dialog import DialogManager
from aiogram_dialog.api.entities.modes import StartMode
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider, setup_dishka

from pybot.core.config import AppSettings
from pybot.core.constants import PointsTypeEnum
from pybot.domain.services.technology_stack import TechnologyLevel
from pybot.dto import UserReadDTO
from pybot.dto.community_checkins import UserNotificationPreferencesDTO
from pybot.dto.technology_stack import TechnologyStackItemDTO
from pybot.dto.value_objects import Points
from pybot.presentation.bot.dialogs.user_reg.states import CreateProfileSG
from pybot.presentation.bot.handlers.profile import user_profile as profile_module
from pybot.presentation.bot.keyboards.main_menu import MainMenuCB, MenuAction
from pybot.presentation.bot.tg_bot_run import setup_handlers as setup_registered_handlers
from pybot.services import TechnologyStackParsingService
from pybot.services.community_checkins import CommunityCheckinService
from pybot.services.community_meetings import CommunityMeetingService
from pybot.services.technology_stack import TechnologyStackService
from pybot.services.user_notification_preferences import UserNotificationPreferencesService
from pybot.services.user_services import UserProfileService, UserService


class _TestUserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            data["user_id"] = user.id
            data["user_roles"] = set()
        return await handler(event, data)


@pytest.mark.asyncio
async def test_stage_1_to_4_handlers_resolve_dishka_through_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise community messages and callbacks through real aiogram and Dishka middleware."""
    user = User(id=100_001, is_bot=False, first_name="Участник")
    user_read = _user_read_dto(user.id)
    profile_service = Mock(spec=UserProfileService)
    profile_service.build_profile_view = AsyncMock(return_value=Mock())
    user_service = Mock(spec=UserService)
    user_service.find_user_by_telegram_id = AsyncMock(return_value=user_read)
    monkeypatch.setattr(profile_module, "render_profile_message", lambda profile: "Профиль участника")

    stack_items = (TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),)
    parsing_service = Mock(spec=TechnologyStackParsingService)
    parsing_service.parse = AsyncMock(side_effect=[(), (), stack_items, stack_items])
    stack_service = Mock(spec=TechnologyStackService)
    stack_service.replace_user_stack = AsyncMock(return_value=stack_items)
    stack_service.recommend_for_learning_items = AsyncMock(return_value=())

    checkin_service = Mock(spec=CommunityCheckinService)
    checkin_service.handle_response = AsyncMock(return_value="Ответ на чек-ин принят")
    meeting_service = Mock(spec=CommunityMeetingService)
    meeting_service.respond = AsyncMock(return_value="Отметил: Приду.")
    notification_preferences_service = Mock(spec=UserNotificationPreferencesService)
    preferences = UserNotificationPreferencesDTO()
    notification_preferences_service.get_preferences = AsyncMock(return_value=preferences)
    notification_preferences_service.set_checkins_enabled = AsyncMock(return_value=True)
    notification_preferences_service.toggle = AsyncMock(return_value=preferences)
    settings = AppSettings.model_construct(
        bot_token="99:test-token",
        role_request_admin_tg_id=1,
        database_url="postgresql+asyncpg://test:test@localhost/test",
    )

    class TestProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def technology_stack_parsing_service(self) -> TechnologyStackParsingService:
            return parsing_service

        @provide(scope=Scope.REQUEST)
        def technology_stack_service(self) -> TechnologyStackService:
            return stack_service

        @provide(scope=Scope.REQUEST)
        def community_checkin_service(self) -> CommunityCheckinService:
            return checkin_service

        @provide(scope=Scope.REQUEST)
        def community_meeting_service(self) -> CommunityMeetingService:
            return meeting_service

        @provide(scope=Scope.REQUEST)
        def user_notification_preferences_service(self) -> UserNotificationPreferencesService:
            return notification_preferences_service

        @provide(scope=Scope.REQUEST)
        def user_profile_service(self) -> UserProfileService:
            return profile_service

        @provide(scope=Scope.REQUEST)
        def user_service(self) -> UserService:
            return user_service

        @provide(scope=Scope.APP)
        def app_settings(self) -> AppSettings:
            return settings

    container = make_async_container(TestProvider(), AiogramProvider())
    dispatcher = Dispatcher()
    stack_entry_router = Router(name="test_stack_entry")

    async def start_stack_dialog(message: Message, dialog_manager: DialogManager) -> None:
        await dialog_manager.start(CreateProfileSG.technology_stack, mode=StartMode.RESET_STACK)
        dialog_manager.dialog_data.update({"stack_owner_telegram_id": user.id, "registered_user_id": 77})

    stack_entry_router.message.register(start_stack_dialog, lambda message: message.text == "/stack")
    dispatcher.include_router(stack_entry_router)
    setup_registered_handlers(dispatcher)
    dispatcher.message.middleware(_TestUserContextMiddleware())
    dispatcher.callback_query.middleware(_TestUserContextMiddleware())
    setup_dishka(container, dispatcher, auto_inject=True)

    bot = Bot("99:test-token")
    sent_messages: list[Message] = []
    answered_callbacks: list[AnswerCallbackQuery] = []

    async def make_request(request_bot: Bot, method: object, timeout: int | None = None) -> object:
        del timeout
        if isinstance(method, SendMessage):
            response = Message(
                message_id=len(sent_messages) + 1,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=request_bot.id, is_bot=True, first_name="Bot"),
                text=method.text,
                reply_markup=(method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None),
            ).as_(request_bot)
            sent_messages.append(response)
            return response
        if isinstance(method, AnswerCallbackQuery):
            answered_callbacks.append(method)
            return True
        if isinstance(method, EditMessageText | EditMessageReplyMarkup):
            message_index = next(
                index for index, message in enumerate(sent_messages) if message.message_id == method.message_id
            )
            current_message = sent_messages[message_index]
            response = current_message.model_copy(
                update={
                    "text": method.text if isinstance(method, EditMessageText) else current_message.text,
                    "reply_markup": method.reply_markup,
                }
            )
            sent_messages[message_index] = response
            return response
        return True

    monkeypatch.setattr(bot.session, "make_request", make_request)
    next_update_id = 0

    async def send_message(text: str) -> None:
        nonlocal next_update_id
        next_update_id += 1
        message = Message(
            message_id=next_update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text=text,
        )
        await dispatcher.feed_update(bot, Update(update_id=next_update_id, message=message))

    async def click(data: str, message: Message | None = None) -> None:
        nonlocal next_update_id
        next_update_id += 1
        callback = CallbackQuery(
            id=f"callback-{next_update_id}",
            from_user=user,
            chat_instance="community-handler-di",
            message=message or sent_messages[-1],
            data=data,
        )
        await dispatcher.feed_update(bot, Update(update_id=next_update_id, callback_query=callback))

    def button_data(message: Message, label: str) -> str:
        if not isinstance(message.reply_markup, InlineKeyboardMarkup):
            raise AssertionError("Expected an inline keyboard")
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.text == label and button.callback_data is not None:
                    return button.callback_data
        raise AssertionError(f"Button {label!r} was not found")

    try:
        await dispatcher.emit_startup(bot=bot)

        # Achievements are displayed by the profile message and menu callback.
        await send_message("/profile")
        profile_message = sent_messages[-1]
        await click(MainMenuCB(action=MenuAction.PROFILE).pack(), profile_message)
        assert profile_service.build_profile_view.await_count == 2

        # Every notification command and the owner-bound callback resolves its service.
        await send_message("/notifications")
        await click(button_data(sent_messages[-1], "✅ Еженедельные чекины"), sent_messages[-1])
        await send_message("/pause_checkin")
        await send_message("/resume_checkin")

        # Private check-in and meeting callbacks are dispatched through their registered routers.
        await send_message("На этой неделе изучаю Python")
        await click("meet:9:going")

        # Empty model parses reply without confirmation and leave the dialog on the same step.
        await send_message("/stack")
        await send_message("как у тебя дела?")
        await send_message("и снова без технологий")
        no_technology_reply = (
            "Не нашёл в сообщении технологий. Напиши, например: "
            "«знаю Python и SQL, изучаю Docker» — или нажми «Пропустить»."
        )
        assert [message.text for message in sent_messages].count(no_technology_reply) == 2
        assert all("Записал:" not in (message.text or "") for message in sent_messages)

        # A command and an inline menu callback exit stack onboarding and continue to their normal handlers.
        await send_message("/notifications")
        await send_message("обычный ответ после команды")
        await send_message("/stack")
        await click(MainMenuCB(action=MenuAction.PROFILE).pack(), sent_messages[-1])
        await send_message("обычный ответ после кнопки меню")

        # Stack edit, save and skip callbacks all resolve/route through the actual dialog.
        await send_message("/stack")
        await send_message("Знаю Python")
        await click(button_data(sent_messages[-1], "Исправить"), sent_messages[-1])
        await send_message("Знаю Python")
        await click(button_data(sent_messages[-1], "Сохранить"), sent_messages[-1])
        await send_message("/stack")
        await click(button_data(sent_messages[-1], "Пропустить"), sent_messages[-1])
    finally:
        await dispatcher.storage.close()
        await container.close()
        await bot.session.close()
        for child_router in dispatcher.sub_routers:
            child_router._parent_router = None

    assert [call.args[0] for call in parsing_service.parse.await_args_list] == [
        "как у тебя дела?",
        "и снова без технологий",
        "Знаю Python",
        "Знаю Python",
    ]
    assert stack_service.replace_user_stack.await_count == 1
    stack_service.replace_user_stack.assert_awaited_once_with(77, stack_items)
    stack_service.recommend_for_learning_items.assert_awaited_once_with(77, stack_items)
    checkin_service.handle_response.assert_any_await(user.id, "На этой неделе изучаю Python")
    checkin_service.handle_response.assert_any_await(user.id, "обычный ответ после команды")
    checkin_service.handle_response.assert_any_await(user.id, "обычный ответ после кнопки меню")
    meeting_service.respond.assert_awaited_once_with(9, user.id, "going")
    assert notification_preferences_service.get_preferences.await_count == 2
    notification_preferences_service.set_checkins_enabled.assert_has_awaits([call(user.id, False), call(user.id, True)])
    notification_preferences_service.toggle.assert_awaited_once()
    assert len(answered_callbacks) >= 5


def _user_read_dto(telegram_id: int) -> UserReadDTO:
    return UserReadDTO(
        id=77,
        first_name="Участник",
        last_name="Тестовый",
        patronymic=None,
        telegram_id=telegram_id,
        academic_points=Points(value=0, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=datetime.now(UTC).date(),
    )
