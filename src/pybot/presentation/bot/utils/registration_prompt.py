"""Helpers for replies to users who have not registered yet."""

from aiogram import Bot
from aiogram.enums import ChatType, ParseMode
from aiogram.types import InlineKeyboardMarkup, Message

from ...texts import REGISTRATION_REQUIRED_MESSAGE
from ..keyboards.main_menu import get_open_bot_registration_keyboard, get_start_registration_keyboard


async def answer_registration_required(
    message: Message,
    bot: Bot,
    text: str = REGISTRATION_REQUIRED_MESSAGE,
    parse_mode: ParseMode | None = ParseMode.HTML,
) -> None:
    """Tell the user to register and attach a button that starts the registration.

    In a private chat the button starts the registration dialog. In a group chat the dialog cannot run,
    so the button opens the private chat with the bot instead.
    """
    keyboard = await _build_registration_keyboard(message, bot)
    await message.answer(text, reply_markup=keyboard, parse_mode=parse_mode)


async def _build_registration_keyboard(message: Message, bot: Bot) -> InlineKeyboardMarkup | None:
    if message.chat.type == ChatType.PRIVATE:
        return get_start_registration_keyboard()

    bot_username = (await bot.me()).username
    if not bot_username:
        return None
    return get_open_bot_registration_keyboard(bot_username)
