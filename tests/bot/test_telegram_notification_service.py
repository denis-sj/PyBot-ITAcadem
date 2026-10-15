from collections.abc import AsyncGenerator
from dataclasses import dataclass
from math import isclose
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from pytest_mock import MockerFixture

from pybot.presentation.bot import get_admin_decision_kb
from pybot.core.config import AppSettings
from pybot.dto import NotifyDTO
from pybot.infrastructure.ports import telegram_notification_service as telegram_notification_module
from pybot.infrastructure.ports.telegram_notification_service import TelegramNotificationService
from pybot.services.ports import NotificationBlockedError, NotificationPermanentError, NotificationTemporaryError

ADMIN_TG_ID = 987_654_321
REQUEST_ID = 15
REQUESTER_USER_ID = 111_222_333
RECIPIENT_USER_ID = 321
RETRY_AFTER_SECONDS = 7.0


@dataclass(slots=True)
class BotFixture:
    bot: Bot
    send_message: AsyncMock


def _dummy_method() -> SendMessage:
    return SendMessage(chat_id=1, text="test")


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@pytest_asyncio.fixture
async def fake_bot(
    mocker: MockerFixture,
    settings_obj: AppSettings,
) -> AsyncGenerator[BotFixture]:
    bot = Bot(token=settings_obj.active_bot_token)
    send_message_mock: AsyncMock = mocker.AsyncMock()
    mocker.patch.object(bot, "send_message", send_message_mock)
    try:
        yield BotFixture(bot=bot, send_message=send_message_mock)
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_send_role_request_to_admin_sends_message_with_keyboard(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = ADMIN_TG_ID

    await service.send_role_request_to_admin(
        request_id=REQUEST_ID,
        requester_user_id=REQUESTER_USER_ID,
        role_name="Admin",
    )

    fake_bot.send_message.assert_awaited_once()
    await_args = fake_bot.send_message.await_args
    _expect(await_args is not None, "send_message has no await args")
    if await_args is None:
        raise AssertionError("send_message has no await args")
    kwargs = await_args.kwargs

    _expect(kwargs.get("chat_id") == ADMIN_TG_ID, "Admin chat id mismatch")
    _expect(kwargs.get("parse_mode") == "HTML", "Parse mode mismatch")
    text = str(kwargs.get("text"))
    _expect("Роль: Admin" in text, "Role name missing in text")
    _expect("Номер запроса" not in text, "Technical request id leaked into text")
    _expect(f"tg://user?id={REQUESTER_USER_ID}" in text, "Mention link missing in text")

    expected_markup = get_admin_decision_kb(REQUEST_ID)
    actual_markup = kwargs.get("reply_markup")
    _expect(actual_markup is not None, "Reply markup is missing")
    _expect(
        actual_markup.model_dump() == expected_markup.model_dump(),
        "Reply markup mismatch",
    )


@pytest.mark.asyncio
async def test_send_role_request_to_admin_maps_retry_after_to_temporary_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramRetryAfter(_dummy_method(), "retry later", int(RETRY_AFTER_SECONDS))
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = 123_456_789

    with pytest.raises(NotificationTemporaryError) as exc_info:
        await service.send_role_request_to_admin(request_id=7, requester_user_id=8, role_name="Mentor")

    retry_after = exc_info.value.retry_after_seconds
    _expect(retry_after is not None, "retry_after_seconds must not be None")
    if retry_after is None:
        raise AssertionError("retry_after_seconds must not be None")
    _expect(isclose(retry_after, RETRY_AFTER_SECONDS), "retry_after_seconds mismatch")
    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_role_request_to_admin_maps_network_error_to_temporary_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramNetworkError(_dummy_method(), "network down")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = 123_456_789

    with pytest.raises(NotificationTemporaryError):
        await service.send_role_request_to_admin(request_id=7, requester_user_id=8, role_name="Mentor")

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_role_request_to_admin_maps_bad_request_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramBadRequest(_dummy_method(), "bad request")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = 123_456_789

    with pytest.raises(NotificationPermanentError):
        await service.send_role_request_to_admin(request_id=7, requester_user_id=8, role_name="Mentor")

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_role_request_to_admin_maps_generic_api_error_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramAPIError(_dummy_method(), "api failure")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = 123_456_789

    with pytest.raises(NotificationPermanentError):
        await service.send_role_request_to_admin(request_id=7, requester_user_id=8, role_name="Mentor")

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_role_request_to_admin_maps_unexpected_error_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = RuntimeError("telegram error")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    settings_obj.role_request_admin_tg_id = 123_456_789

    with pytest.raises(NotificationPermanentError, match="Unexpected notification delivery failure"):
        await service.send_role_request_to_admin(request_id=7, requester_user_id=8, role_name="Mentor")

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_sends_trimmed_text(fake_bot: BotFixture, settings_obj: AppSettings) -> None:
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="  hello  "))

    fake_bot.send_message.assert_awaited_once_with(
        chat_id=RECIPIENT_USER_ID, message_thread_id=None, text="hello", parse_mode=None
    )


@pytest.mark.asyncio
async def test_send_message_passes_parse_mode_only_when_set(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello", parse_mode="HTML"))

    fake_bot.send_message.assert_awaited_once_with(
        chat_id=RECIPIENT_USER_ID,
        message_thread_id=None,
        text="hello",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_send_message_raises_on_blank_text(fake_bot: BotFixture) -> None:
    with pytest.raises(ValueError, match="message must not be empty"):
        NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="   ")

    fake_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_maps_retry_after_to_temporary_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramRetryAfter(_dummy_method(), "retry later", int(RETRY_AFTER_SECONDS))
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationTemporaryError) as exc_info:
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))

    retry_after = exc_info.value.retry_after_seconds
    _expect(retry_after is not None, "retry_after_seconds must not be None")
    if retry_after is None:
        raise AssertionError("retry_after_seconds must not be None")
    _expect(isclose(retry_after, RETRY_AFTER_SECONDS), "retry_after_seconds mismatch")
    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_maps_network_error_to_temporary_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramNetworkError(_dummy_method(), "network down")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationTemporaryError):
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_maps_forbidden_to_blocked_recipient(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramForbiddenError(_dummy_method(), "bot was blocked by the user")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationBlockedError) as exc_info:
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))

    assert isinstance(exc_info.value, NotificationPermanentError)
    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_maps_bad_request_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramBadRequest(_dummy_method(), "bad request")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationPermanentError):
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_maps_generic_api_error_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = TelegramAPIError(_dummy_method(), "api failure")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationPermanentError):
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))

    fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_maps_unexpected_error_to_permanent_error(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    fake_bot.send_message.side_effect = RuntimeError("send failed")
    service = TelegramNotificationService(fake_bot.bot, settings_obj)

    with pytest.raises(NotificationPermanentError, match="Unexpected notification delivery failure"):
        await service.send_message(NotifyDTO(recipient_id=RECIPIENT_USER_ID, message="hello"))


@pytest.mark.asyncio
async def test_send_attendance_notification_with_photo_button(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    await service.send_attendance_notification(
        recipient_tg_id=123,
        text="<b>Уведомление</b>",
        import_id=42,
    )
    fake_bot.send_message.assert_awaited_once()
    call_kwargs = fake_bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 123
    assert call_kwargs["text"] == "<b>Уведомление</b>"
    assert call_kwargs["parse_mode"] == "HTML"
    reply_markup = call_kwargs["reply_markup"]
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].text == "📷 Номер фото #42"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "log_method"),
    (
        (TelegramNetworkError(_dummy_method(), "PRIVATE-TELEGRAM-DETAIL"), "warning"),
        (RuntimeError("PRIVATE-TELEGRAM-DETAIL"), "error"),
    ),
)
async def test_send_message_failure_logs_no_recipient_or_message(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    log_method: str,
) -> None:
    recipient_id = 765_432_109
    private_message = "PRIVATE-CHECKIN-GOAL"
    fake_bot.send_message.side_effect = error
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    safe_logger = Mock()
    monkeypatch.setattr(telegram_notification_module, "logger", safe_logger)

    with pytest.raises((NotificationTemporaryError, NotificationPermanentError)):
        await service.send_message(NotifyDTO(recipient_id=recipient_id, message=private_message))

    logged_call = getattr(safe_logger, log_method).call_args
    assert logged_call is not None
    assert str(recipient_id) not in repr(logged_call)
    assert private_message not in repr(logged_call)
    assert "PRIVATE-TELEGRAM-DETAIL" not in repr(logged_call)


@pytest.mark.asyncio
async def test_send_attendance_notification_without_import_id(
    fake_bot: BotFixture,
    settings_obj: AppSettings,
) -> None:
    service = TelegramNotificationService(fake_bot.bot, settings_obj)
    await service.send_attendance_notification(
        recipient_tg_id=123,
        text="Уведомление",
        import_id=None,
    )
    fake_bot.send_message.assert_awaited_once()
    call_kwargs = fake_bot.send_message.call_args[1]
    assert call_kwargs["reply_markup"] is None

    fake_bot.send_message.assert_awaited_once()
