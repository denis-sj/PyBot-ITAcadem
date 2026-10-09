"""Common navigation handlers for bot messages and inline keyboards."""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .....core import logger
from ....texts import CARD_NOT_OWNER_ALERT
from ...keyboards.navigation import CommonNavigationCB

navigation_router = Router(name="navigation")


@navigation_router.callback_query(CommonNavigationCB.filter(F.action == "close"))
async def cb_close_message(callback: CallbackQuery, state: FSMContext, callback_data: CommonNavigationCB) -> None:
    """Close the current message by deleting it or removing its inline keyboard.

    Personal cards carry their owner's Telegram ID in ``callback_data.target``; when another user
    presses the button in a shared chat, nothing is closed and an alert is shown instead.

    Attempts to delete the message. If Telegram rejects deletion (e.g. message too old,
    insufficient permissions, or already removed), falls back to clearing the reply markup.
    Always acknowledges the callback to dismiss loading indicator.
    """
    if callback_data.target and callback_data.target != str(callback.from_user.id):
        await callback.answer(CARD_NOT_OWNER_ALERT, show_alert=True)
        return

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
