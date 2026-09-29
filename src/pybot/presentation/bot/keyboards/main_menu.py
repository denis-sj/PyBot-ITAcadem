"""Inline main menu of the bot and the registration prompt button."""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ...texts import (
    BUTTON_BROADCAST_SEND,
    BUTTON_CANCEL,
    BUTTON_START_REGISTRATION,
    MAIN_MENU_BUTTON_BROADCAST,
    MAIN_MENU_BUTTON_HELP,
    MAIN_MENU_BUTTON_LEADERBOARD,
    MAIN_MENU_BUTTON_PROFILE,
    MAIN_MENU_BUTTON_VISITS,
)


class MenuAction(StrEnum):
    """Actions available from the main menu and its broadcast scenario."""

    PROFILE = "profile"
    VISITS = "visits"
    LEADERBOARD = "leaderboard"
    HELP = "help"
    REGISTER = "register"
    BROADCAST = "broadcast"
    BROADCAST_SEND = "bc_send"
    BROADCAST_CANCEL = "bc_cancel"


class MainMenuCB(CallbackData, prefix="menu"):
    """Callback data for main menu buttons."""

    action: MenuAction


def get_main_menu_keyboard(*, show_broadcast: bool = False) -> InlineKeyboardMarkup:
    """Return the main menu; the broadcast button is only for users allowed to broadcast."""
    rows = [
        [
            _menu_button(MAIN_MENU_BUTTON_PROFILE, MenuAction.PROFILE),
            _menu_button(MAIN_MENU_BUTTON_VISITS, MenuAction.VISITS),
        ],
        [
            _menu_button(MAIN_MENU_BUTTON_LEADERBOARD, MenuAction.LEADERBOARD),
            _menu_button(MAIN_MENU_BUTTON_HELP, MenuAction.HELP),
        ],
    ]
    if show_broadcast:
        rows.append([_menu_button(MAIN_MENU_BUTTON_BROADCAST, MenuAction.BROADCAST)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_start_registration_keyboard() -> InlineKeyboardMarkup:
    """Return a button that starts the registration dialog; for private chats with the bot."""
    button = _menu_button(BUTTON_START_REGISTRATION, MenuAction.REGISTER, style="success")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def get_open_bot_registration_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    """Return a link button that opens the private chat with the bot, where ``/start`` begins registration."""
    link_button = InlineKeyboardButton(
        text=BUTTON_START_REGISTRATION,
        url=f"https://t.me/{bot_username}?start=register",
    )
    return InlineKeyboardMarkup(inline_keyboard=[[link_button]])


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Return buttons that confirm or cancel the drafted broadcast."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _menu_button(BUTTON_BROADCAST_SEND, MenuAction.BROADCAST_SEND, style="success"),
                _menu_button(BUTTON_CANCEL, MenuAction.BROADCAST_CANCEL, style="danger"),
            ]
        ]
    )


def get_broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    """Return a single button that abandons the broadcast scenario."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[_menu_button(BUTTON_CANCEL, MenuAction.BROADCAST_CANCEL, style="danger")]]
    )


def _menu_button(text: str, action: MenuAction, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MainMenuCB(action=action).pack(), style=style)
