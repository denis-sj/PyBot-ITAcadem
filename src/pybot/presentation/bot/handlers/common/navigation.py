"""Common navigation handlers for bot messages and inline keyboards."""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .....core import logger
from ...keyboards.navigation import CommonNavigationCB

navigation_router = Router(name="navigation")


@navigation_router.callback_query(CommonNavigationCB.filter(F.action == "close"))
async def cb_close_message(callback: CallbackQuery, state: FSMContext) -> None:
    """Close the current message by deleting it or removing its inline keyboard.

    Attempts to delete the message. If Telegram rejects deletion (e.g. message too old,
    insufficient permissions, or already removed), falls back to clearing the reply markup.
    Always acknowledges the callback to dismiss loading indicator.
    """
    await state.clear()
    if callback.message and isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            with contextlib.suppress(TelegramBadRequest):
                await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.exception("Unexpected error while deleting message on close")

    await callback.answer()
