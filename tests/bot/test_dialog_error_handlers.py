from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from pybot.presentation.bot.handlers.common.dialog_errors import register_dialog_error_handlers
from pybot.presentation.texts.bot_errors import BOT_UNEXPECTED_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "chat_id", "expected_message_count"),
    [("private", 100_001, 1), ("group", -100_001, 0)],
)
async def test_failed_message_handler_replies_only_in_private_chat(
    monkeypatch: pytest.MonkeyPatch,
    chat_type: str,
    chat_id: int,
    expected_message_count: int,
) -> None:
    dispatcher = Dispatcher()
    router = Router(name="failing_message_handler")

    @router.message(F.text == "fail")
    async def fail_message_handler(message: Message) -> None:
        del message
        raise RuntimeError("simulated handler failure")

    dispatcher.include_router(router)
    register_dialog_error_handlers(dispatcher)
    bot = Bot("99:test-token")
    sent_texts: list[str] = []

    async def make_request(request_bot: Bot, method: object, timeout: int | None = None) -> object:
        del timeout
        if isinstance(method, SendMessage):
            sent_texts.append(method.text)
            return Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type=chat_type),
                from_user=User(id=request_bot.id, is_bot=True, first_name="Bot"),
                text=method.text,
            ).as_(request_bot)
        return True

    monkeypatch.setattr(bot.session, "make_request", make_request)
    try:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=chat_id, type=chat_type),
                    from_user=User(id=100_001, is_bot=False, first_name="Участник"),
                    text="fail",
                ),
            ),
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()

    assert len(sent_texts) == expected_message_count
    if expected_message_count:
        assert sent_texts == [BOT_UNEXPECTED_ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "chat_id", "expected_message_count"),
    [("private", 100_001, 1), ("group", -100_001, 0)],
)
async def test_failed_callback_is_answered_and_only_private_chat_gets_text(
    monkeypatch: pytest.MonkeyPatch,
    chat_type: str,
    chat_id: int,
    expected_message_count: int,
) -> None:
    dispatcher = Dispatcher()
    router = Router(name="failing_callback_handler")

    @router.callback_query(F.data == "fail")
    async def fail_callback_handler(callback: CallbackQuery) -> None:
        del callback
        raise RuntimeError("simulated callback failure")

    dispatcher.include_router(router)
    register_dialog_error_handlers(dispatcher)
    bot = Bot("99:test-token")
    sent_texts: list[str] = []
    answered_callbacks: list[str] = []

    async def make_request(request_bot: Bot, method: object, timeout: int | None = None) -> object:
        del timeout
        if isinstance(method, SendMessage):
            sent_texts.append(method.text)
            return Message(
                message_id=2,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type=chat_type),
                from_user=User(id=request_bot.id, is_bot=True, first_name="Bot"),
                text=method.text,
            ).as_(request_bot)
        if isinstance(method, AnswerCallbackQuery):
            answered_callbacks.append(method.callback_query_id)
            return True
        return True

    monkeypatch.setattr(bot.session, "make_request", make_request)
    callback_message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
        text="Кнопка",
    )
    try:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                callback_query=CallbackQuery(
                    id="callback-fail",
                    from_user=User(id=100_001, is_bot=False, first_name="Участник"),
                    chat_instance="error-handler-test",
                    message=callback_message,
                    data="fail",
                ),
            ),
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()

    assert answered_callbacks == ["callback-fail"]
    assert len(sent_texts) == expected_message_count
    if expected_message_count:
        assert sent_texts == [BOT_UNEXPECTED_ERROR]
