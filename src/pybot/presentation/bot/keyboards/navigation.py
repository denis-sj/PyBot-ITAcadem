"""Common navigation callbacks and keyboards."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ...texts import BUTTON_CLOSE


class CommonNavigationCB(CallbackData, prefix="nav"):
    """Callback data for common navigation actions (e.g. close, back)."""

    action: str
    target: str | None = None


def get_close_button(text: str = BUTTON_CLOSE) -> InlineKeyboardButton:
    """Return a single inline button that closes the message."""
    return InlineKeyboardButton(
        text=text,
        callback_data=CommonNavigationCB(action="close").pack(),
    )


def get_close_keyboard(text: str = BUTTON_CLOSE) -> InlineKeyboardMarkup:
    """Return an inline keyboard with a single close button."""
    return InlineKeyboardMarkup(inline_keyboard=[[get_close_button(text=text)]])
