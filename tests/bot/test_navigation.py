"""Unit tests for common navigation handlers, keyboards, and callbacks."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from pybot.presentation.bot.handlers.common.navigation import cb_close_message
from pybot.presentation.bot.keyboards.navigation import (
    CommonNavigationCB,
    get_close_button,
    get_close_keyboard,
)
from pybot.presentation.texts import BUTTON_CLOSE


def test_common_navigation_cb_packing() -> None:
    cb = CommonNavigationCB(action="close")
    packed = cb.pack()
    unpacked = CommonNavigationCB.unpack(packed)
    assert unpacked.action == "close"
    assert unpacked.target is None


def test_get_close_button() -> None:
    btn = get_close_button()
    assert btn.text == BUTTON_CLOSE
    assert btn.callback_data is not None
    unpacked = CommonNavigationCB.unpack(btn.callback_data)
    assert unpacked.action == "close"


def test_get_close_keyboard() -> None:
    kb = get_close_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    assert kb.inline_keyboard[0][0].text == BUTTON_CLOSE


@pytest.mark.asyncio
async def test_cb_close_message_deletes_successfully() -> None:
    message_mock = AsyncMock(spec=Message)
    delete_mock = AsyncMock()
    message_mock.delete = delete_mock
    edit_markup_mock = AsyncMock()
    message_mock.edit_reply_markup = edit_markup_mock

    callback = AsyncMock(spec=CallbackQuery)
    callback.message = message_mock
    answer_mock = AsyncMock()
    callback.answer = answer_mock

    await cb_close_message(callback)

    delete_mock.assert_awaited_once()
    edit_markup_mock.assert_not_awaited()
    answer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_close_message_fallback_to_edit_reply_markup_on_bad_request() -> None:
    message_mock = AsyncMock(spec=Message)
    delete_mock = AsyncMock(
        side_effect=TelegramBadRequest(
            method=DeleteMessage(chat_id=1, message_id=1), message="message can't be deleted"
        )
    )
    message_mock.delete = delete_mock
    edit_markup_mock = AsyncMock()
    message_mock.edit_reply_markup = edit_markup_mock

    callback = AsyncMock(spec=CallbackQuery)
    callback.message = message_mock
    answer_mock = AsyncMock()
    callback.answer = answer_mock

    await cb_close_message(callback)

    delete_mock.assert_awaited_once()
    edit_markup_mock.assert_awaited_once_with(reply_markup=None)
    answer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_close_message_suppresses_fallback_exception() -> None:
    message_mock = AsyncMock(spec=Message)
    delete_mock = AsyncMock(
        side_effect=TelegramBadRequest(
            method=DeleteMessage(chat_id=1, message_id=1), message="message can't be deleted"
        )
    )
    edit_markup_mock = AsyncMock(
        side_effect=TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=1, message_id=1), message="message is not modified"
        )
    )
    message_mock.delete = delete_mock
    message_mock.edit_reply_markup = edit_markup_mock

    callback = AsyncMock(spec=CallbackQuery)
    callback.message = message_mock
    answer_mock = AsyncMock()
    callback.answer = answer_mock

    # Should not raise exception
    await cb_close_message(callback)

    delete_mock.assert_awaited_once()
    edit_markup_mock.assert_awaited_once_with(reply_markup=None)
    answer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_close_message_handles_none_message() -> None:
    callback = AsyncMock(spec=CallbackQuery)
    callback.message = None
    answer_mock = AsyncMock()
    callback.answer = answer_mock

    await cb_close_message(callback)

    answer_mock.assert_awaited_once()
