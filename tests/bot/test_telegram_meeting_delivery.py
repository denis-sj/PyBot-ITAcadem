"""Telegram meeting delivery preserves RSVP controls and maps blocked recipients."""

from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from pybot.infrastructure.ports.telegram_meeting_delivery import TelegramMeetingDelivery
from pybot.services.ports import NotificationBlockedError


def _bot_with_send_message(*, error: Exception | None = None) -> Mock:
    bot = Mock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=error)
    return bot


@pytest.mark.asyncio
async def test_private_invitation_has_the_expected_answer_buttons() -> None:
    bot = _bot_with_send_message()
    delivery = TelegramMeetingDelivery(bot)

    await delivery.send_invitation(77, "Встреча завтра", 42)

    call = bot.send_message.await_args
    assert call is not None
    assert call.kwargs["chat_id"] == 77
    markup = call.kwargs["reply_markup"]
    assert [[button.text for button in row] for row in markup.inline_keyboard] == [["Приду", "Не смогу"]]
    assert [button.callback_data for button in markup.inline_keyboard[0]] == ["meet:42:going", "meet:42:not_going"]


@pytest.mark.asyncio
async def test_forbidden_telegram_response_is_reported_as_a_blocked_user() -> None:
    error = TelegramForbiddenError(SendMessage(chat_id=77, text="Приглашение"), "bot was blocked")
    bot = _bot_with_send_message(error=error)
    delivery = TelegramMeetingDelivery(bot)

    with pytest.raises(NotificationBlockedError):
        await delivery.send_invitation(77, "Приглашение", 42)
