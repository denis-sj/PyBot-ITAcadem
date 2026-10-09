"""Common navigation callbacks and keyboards."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ...texts import BUTTON_CLOSE


class CommonNavigationCB(CallbackData, prefix="nav"):
    """Callback data for common navigation actions (e.g. close, back)."""

    action: str
    target: str | None = None


def get_close_button(text: str = BUTTON_CLOSE, owner_id: int | None = None) -> InlineKeyboardButton:
    """Return a single inline button that closes the message.

    Args:
        text: Button label.
        owner_id: Telegram ID of the only user allowed to close the message. Use it for personal
            cards that may be opened in a group chat; ``None`` lets anyone close the message.
    """
    return InlineKeyboardButton(
        text=text,
        callback_data=CommonNavigationCB(action="close", target=None if owner_id is None else str(owner_id)).pack(),
        style="danger",
    )


def get_close_keyboard(text: str = BUTTON_CLOSE) -> InlineKeyboardMarkup:
    """Return an inline keyboard with a single close button."""
    return InlineKeyboardMarkup(inline_keyboard=[[get_close_button(text=text)]])
