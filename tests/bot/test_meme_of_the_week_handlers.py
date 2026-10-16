from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Message

from pybot.presentation.bot.handlers.meme_of_the_week import (
    handle_meme_next,
    handle_meme_now,
    weekly_meme_router,
)
from pybot.services.meme_of_the_week import MemeOfTheWeekService, MemePublicationResult, MemePublicationStatus
from tests.agent.conftest import build_message


@pytest.mark.asyncio
async def test_meme_commands_call_service_and_send_plain_text() -> None:
    message = Mock(spec=Message)
    message.from_user = SimpleNamespace(id=8877)
    message.answer = AsyncMock()
    service = Mock(spec=MemeOfTheWeekService)
    service.publish_test = AsyncMock(return_value=MemePublicationResult(MemePublicationStatus.PUBLISHED))
    service.publish_replacement = AsyncMock(return_value=MemePublicationResult(MemePublicationStatus.PUBLISHED))

    await handle_meme_now(message, service)
    await handle_meme_next(message, service)

    service.publish_test.assert_awaited_once_with()
    service.publish_replacement.assert_awaited_once_with(replacer_telegram_id=8877)
    assert [call.args[0] for call in message.answer.await_args_list] == [
        "Тестовый мем опубликован.",
        "Мем заменён.",
    ]
    assert all(call.kwargs == {"parse_mode": None} for call in message.answer.await_args_list)


def test_both_meme_commands_require_admin_and_next_is_private_only() -> None:
    handlers = weekly_meme_router.message.handlers
    now_handler = next(handler for handler in handlers if len(handler.filters or []) == 1)
    next_handler = next(handler for handler in handlers if len(handler.filters or []) == 2)

    assert now_handler.flags["role"] == "Admin"
    assert next_handler.flags["role"] == "Admin"
    next_filters = next_handler.filters
    assert next_filters is not None
    private_filter = next_filters[1].magic
    assert private_filter is not None
    assert private_filter.resolve(build_message(chat_type="private", chat_id=700, topic_id=None)) is True
    assert private_filter.resolve(build_message(chat_type="supergroup")) is False
    now_filters = now_handler.filters
    assert now_filters is not None
    assert len(now_filters) == 1
