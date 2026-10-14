"""Safe periodic Telegram typing status for long-running agent work."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from aiogram import Bot

from .....core import logger

_TYPING_INTERVAL_S = 4.0


@asynccontextmanager
async def agent_typing_status(bot: Bot, chat_id: int, message_thread_id: int | None) -> AsyncIterator[None]:
    """Send typing actions until the agent operation completes or raises."""
    task = asyncio.create_task(
        _send_typing_actions(bot, chat_id, message_thread_id),
        name="agent-typing-status",
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _send_typing_actions(bot: Bot, chat_id: int, message_thread_id: int | None) -> None:
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing", message_thread_id=message_thread_id)
        except Exception as error:
            logger.debug(
                "event=agent_typing status=failed error_type={error_type}",
                error_type=type(error).__name__,
            )
        await asyncio.sleep(_TYPING_INTERVAL_S)
