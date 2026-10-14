from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, Chat, Message, User

from pybot.domain.services.community_checkins import NotificationPreferenceKey
from pybot.dto.community_checkins import UserNotificationPreferencesDTO
from pybot.presentation.bot.handlers.community_checkins import handle_private_checkin_response
from pybot.presentation.bot.handlers.notification_preferences import (
    NotificationPreferenceCB,
    cb_toggle_notification,
)
from pybot.services.community_checkins import CommunityCheckinService
from pybot.services.user_notification_preferences import UserNotificationPreferencesService


def _message(text: str, *, telegram_id: int = 100_001) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_id, type="private"),
        from_user=User(id=telegram_id, is_bot=False, first_name="Участник"),
        text=text,
    )


def _callback(telegram_id: int, message: Message) -> CallbackQuery:
    return CallbackQuery(
        id="notification-setting",
        from_user=User(id=telegram_id, is_bot=False, first_name="Участник"),
        chat_instance="notification-preferences",
        message=message,
    )


@pytest.mark.asyncio
async def test_private_checkin_response_is_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message("На этой неделе изучаю Python")
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    service = AsyncMock(spec=CommunityCheckinService)
    service.handle_response.return_value = "Записал, удачи на неделе!"

    await handle_private_checkin_response(message, service, user_id=42)

    service.handle_response.assert_awaited_once_with(42, "На этой неделе изучаю Python")
    answer.assert_awaited_once_with("Записал, удачи на неделе!")


@pytest.mark.asyncio
async def test_checkin_router_leaves_dialog_messages_unhandled() -> None:
    service = AsyncMock(spec=CommunityCheckinService)

    result = await handle_private_checkin_response(
        _message("Ответ в диалоге"),
        service,
        user_id=42,
        raw_state="registration:stack",
    )

    assert result is UNHANDLED
    service.handle_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_user_cannot_toggle_notification_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    callback = _callback(telegram_id=100_002, message=_message("Настройки"))
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    service = AsyncMock(spec=UserNotificationPreferencesService)

    await cb_toggle_notification(
        callback,
        NotificationPreferenceCB(setting=NotificationPreferenceKey.CHECKINS.value, owner_telegram_id=100_001),
        service,
        user_id=42,
    )

    service.toggle.assert_not_awaited()
    answer.assert_awaited_once_with("Эти настройки открыты другим участником.", show_alert=True)


@pytest.mark.asyncio
async def test_owner_can_toggle_notification_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message("Настройки")
    callback = _callback(telegram_id=100_001, message=message)
    answer = AsyncMock()
    edit_text = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", edit_text)
    service = AsyncMock(spec=UserNotificationPreferencesService)
    service.toggle.return_value = UserNotificationPreferencesDTO(checkins_enabled=False)

    await cb_toggle_notification(
        callback,
        NotificationPreferenceCB(setting=NotificationPreferenceKey.CHECKINS.value, owner_telegram_id=100_001),
        service,
        user_id=42,
    )

    service.toggle.assert_awaited_once_with(42, NotificationPreferenceKey.CHECKINS)
    edit_text.assert_awaited_once()
    assert edit_text.await_args is not None
    assert "чекины" in edit_text.await_args.args[0].casefold()
    answer.assert_awaited_once_with("Еженедельные чекины обновлены.")
