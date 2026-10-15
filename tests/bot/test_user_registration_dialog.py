from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User
from aiogram_dialog import DialogManager, setup_dialogs
from aiogram_dialog.api.entities.modes import StartMode
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider, setup_dishka

from pybot.dto import UserReadDTO
from pybot.dto.value_objects import Points
from pybot.core.constants import PointsTypeEnum
from pybot.presentation.bot import registration_handlers_module
from pybot.presentation.bot.dialogs import user_router
from pybot.presentation.bot.dialogs.user_reg.states import CreateProfileSG
from pybot.presentation.texts import REGISTRATION_INTERNAL_ERROR
from pybot.services import UserRegistrationService
from pybot.services.user_services import UserProfileService


@pytest.mark.asyncio
async def test_competence_finish_and_skip_buttons_are_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    created_users = [
        _user_read_dto(100_001, "Иван"),
        _user_read_dto(100_002, "Пётр"),
    ]
    registration_service = Mock(spec=UserRegistrationService)
    registration_service.register_student = AsyncMock(
        side_effect=[*created_users, RuntimeError("database unavailable")]
    )
    profile_service = Mock(spec=UserProfileService)
    profile_service.build_profile_view = AsyncMock(return_value=Mock())
    monkeypatch.setattr(registration_handlers_module, "render_profile_message", Mock(return_value="Профиль"))
    log = Mock()
    monkeypatch.setattr(registration_handlers_module, "logger", log)

    class TestProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def user_registration_service(self) -> UserRegistrationService:
            return registration_service

        @provide(scope=Scope.REQUEST)
        def user_profile_service(self) -> UserProfileService:
            return profile_service

    container = make_async_container(TestProvider(), AiogramProvider())
    dispatcher = Dispatcher()
    router = Router()

    async def start_registration(message: Message, dialog_manager: DialogManager) -> None:
        assert message.from_user is not None
        await dialog_manager.start(
            CreateProfileSG.competencies,
            data={"registration_competencies": [{"id": 1, "name": "Python"}]},
            mode=StartMode.RESET_STACK,
        )
        dialog_manager.dialog_data.update(
            {
                "phone_number": "+79990001122",
                "tg_id": message.from_user.id,
                "telegram_username": None,
                "first_name": "Tester",
                "last_name": "User",
                "patronymic": None,
                "competence_ids": [],
            }
        )

    router.message.register(start_registration, Command("register"))
    router.include_router(user_router)
    dispatcher.include_router(router)
    setup_dialogs(dispatcher)
    setup_dishka(container, dispatcher, auto_inject=True)
    bot = Bot("99:test-token")
    sent_messages: list[Message] = []
    answered_callbacks: list[str] = []

    async def make_request(request_bot: Bot, method: object, timeout: int | None = None) -> object:
        del timeout
        if isinstance(method, AnswerCallbackQuery):
            answered_callbacks.append(method.callback_query_id)
            return True
        if isinstance(method, SendMessage):
            message = Message(
                message_id=len(sent_messages) + 1,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=request_bot.id, is_bot=True, first_name="Bot"),
                text=method.text,
                reply_markup=(method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None),
            ).as_(request_bot)
            sent_messages.append(message)
            return message
        if isinstance(method, EditMessageText | EditMessageReplyMarkup):
            message_index = next(
                index for index, message in enumerate(sent_messages) if message.message_id == method.message_id
            )
            message = sent_messages[message_index]
            updated_message = message.model_copy(
                update={
                    "text": method.text if isinstance(method, EditMessageText) else message.text,
                    "reply_markup": method.reply_markup,
                }
            )
            sent_messages[message_index] = updated_message
            return updated_message
        return True

    monkeypatch.setattr(bot.session, "make_request", make_request)

    try:
        await dispatcher.emit_startup(bot=bot)
        for update_id, user_id, button_text in (
            (1, 100_001, "Завершить регистрацию"),
            (3, 100_002, "Пропустить"),
            (5, 100_003, "Завершить регистрацию"),
        ):
            user = User(id=user_id, is_bot=False, first_name="Tester")
            start_message = Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=Chat(id=user_id, type="private"),
                from_user=user,
                text="/register",
            )
            await dispatcher.feed_update(bot, Update(update_id=update_id, message=start_message))
            dialog_message = sent_messages[-1]
            assert isinstance(dialog_message.reply_markup, InlineKeyboardMarkup)
            button = next(
                button
                for row in dialog_message.reply_markup.inline_keyboard
                for button in row
                if button.text == button_text
            )
            callback = CallbackQuery(
                id=f"click-{update_id}",
                from_user=user,
                chat_instance="test-chat",
                message=dialog_message,
                data=button.callback_data,
            )
            await dispatcher.feed_update(bot, Update(update_id=update_id + 1, callback_query=callback))
    finally:
        await dispatcher.storage.close()
        await container.close()
        await bot.session.close()

    assert registration_service.register_student.await_count == 3
    submitted_dtos = [call.args[0] for call in registration_service.register_student.await_args_list]
    assert [dto.user.tg_id for dto in submitted_dtos] == [100_001, 100_002, 100_003]
    assert [dto.competence_ids for dto in submitted_dtos] == [[], [], []]
    assert profile_service.build_profile_view.await_count == 2
    assert answered_callbacks == ["click-1", "click-3", "click-5"]
    assert any(message.text == REGISTRATION_INTERNAL_ERROR for message in sent_messages)
    log.exception.assert_called_once_with("event=registration phase=completion status=failed")


def _user_read_dto(telegram_id: int, first_name: str) -> UserReadDTO:
    return UserReadDTO(
        id=telegram_id,
        first_name=first_name,
        last_name="User",
        patronymic=None,
        telegram_id=telegram_id,
        academic_points=Points(value=0, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=datetime.now(UTC).date(),
    )
