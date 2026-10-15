"""Telegram meeting delivery preserves RSVP controls and maps blocked recipients."""

from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from pybot.infrastructure.ports import telegram_meeting_delivery as telegram_meeting_module
from pybot.infrastructure.ports.telegram_meeting_delivery import TelegramMeetingDelivery
from pybot.services.ports import NotificationBlockedError, NotificationPermanentError


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
    assert call.kwargs["parse_mode"] is None
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


@pytest.mark.asyncio
async def test_unexpected_send_failure_log_contains_no_recipient_or_message(mocker) -> None:
    private_message = "PRIVATE-MEETING-TITLE"
    bot = _bot_with_send_message(error=RuntimeError("PRIVATE-TELEGRAM-DETAIL"))
    delivery = TelegramMeetingDelivery(bot)
    warning_mock = mocker.patch.object(telegram_meeting_module.logger, "warning")

    with pytest.raises(NotificationPermanentError):
        await delivery.send_invitation(987654321, private_message, 42)

    warning_mock.assert_called_once()
    assert "987654321" not in repr(warning_mock.call_args)
    assert private_message not in repr(warning_mock.call_args)
    assert "PRIVATE-TELEGRAM-DETAIL" not in repr(warning_mock.call_args)
