"""Модуль бота IT Academ."""

from aiogram import Dispatcher
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent, Message
from aiogram_dialog.api.exceptions import UnknownIntent

from .....core import logger
from ....texts import STALE_DIALOG_MESSAGE
from ....texts.bot_errors import BOT_UNEXPECTED_ERROR


# TODO Проверить
async def handle_unknown_intent(event: ErrorEvent) -> bool:
    """Show the stale-dialog alert or route non-callback errors to the fallback handler."""
    callback: CallbackQuery | None = event.update.callback_query
    if callback is None:
        return await handle_unexpected_error(event)

    logger.warning(
        "Handled stale dialog callback. update_id={update_id}, callback_id={callback_id}, from_user={from_user_id}",
        update_id=event.update.update_id,
        callback_id=callback.id,
        from_user_id=callback.from_user.id,
    )

    try:
        await callback.answer(STALE_DIALOG_MESSAGE, show_alert=True)
    except Exception as exc:
        logger.warning("Failed to answer stale callback query: {error_type}", error_type=type(exc).__name__)
    return True


async def handle_unexpected_error(event: ErrorEvent) -> bool:
    """Log update failures and send a fallback only in private chats."""
    logger.exception(
        "event=telegram_update status=failed update_id={update_id} exception_type={exception_type}",
        update_id=event.update.update_id,
        exception_type=type(event.exception).__name__,
    )

    callback = event.update.callback_query
    if callback is not None:
        callback_message = callback.message
        if callback_message is not None and callback_message.chat.type == "private":
            if isinstance(callback_message, Message):
                await _send_error_message(callback_message)
            else:
                await _send_callback_error_message(callback)
        try:
            await callback.answer()
        except Exception as error:
            logger.warning(
                "event=telegram_error_response status=failed operation=answer_callback error_type={error_type}",
                error_type=type(error).__name__,
            )
        return True

    message = event.update.message
    if message is not None and message.chat.type == "private":
        await _send_error_message(message)
    return True


async def _send_error_message(message: Message) -> None:
    try:
        await message.answer(BOT_UNEXPECTED_ERROR)
    except Exception as error:
        logger.warning(
            "event=telegram_error_response status=failed operation=send_message error_type={error_type}",
            error_type=type(error).__name__,
        )


async def _send_callback_error_message(callback: CallbackQuery) -> None:
    try:
        bot = callback.bot
        if callback.message is not None and bot is not None:
            await bot.send_message(chat_id=callback.message.chat.id, text=BOT_UNEXPECTED_ERROR)
    except Exception as error:
        logger.warning(
            "event=telegram_error_response status=failed operation=send_callback_message error_type={error_type}",
            error_type=type(error).__name__,
        )


def register_dialog_error_handlers(dp: Dispatcher) -> None:
    """Register specific stale-dialog handling before the general failure fallback."""
    dp.errors.register(handle_unknown_intent, ExceptionTypeFilter(UnknownIntent))
    dp.errors.register(handle_unexpected_error, ExceptionTypeFilter(Exception))
