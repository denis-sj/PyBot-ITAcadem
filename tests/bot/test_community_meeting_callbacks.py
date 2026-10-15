from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from pybot.domain.exceptions import DomainError
from pybot.presentation.bot.handlers.community_meetings import handle_meeting_response
from pybot.services.community_meetings import CommunityMeetingService


def _callback(data: str) -> CallbackQuery:
    telegram_user = User(id=100_001, is_bot=False, first_name="Участник")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100_001, type="private"),
        from_user=telegram_user,
        text="Встреча",
    )
    return CallbackQuery(
        id="meeting-rsvp",
        from_user=telegram_user,
        chat_instance="meeting-rsvp-test",
        message=message,
        data=data,
    )


@pytest.mark.asyncio
async def test_nonexistent_meeting_callback_returns_alert_without_logging_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = _callback("meet:999999:going")
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    service = AsyncMock(spec=CommunityMeetingService)
    service.respond.side_effect = DomainError("Встреча не найдена.")

    await handle_meeting_response(callback, service, user_id=42)

    service.respond.assert_awaited_once_with(999999, 42, "going")
    answer.assert_awaited_once_with("Встреча не найдена.", show_alert=True)
