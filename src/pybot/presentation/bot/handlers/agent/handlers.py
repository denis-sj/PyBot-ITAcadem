"""Thin Telegram entrypoints for questions and author-owned confirmations."""

from aiogram import Bot, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from dishka import FromDishka

from .....core import logger
from .....core.config import AppSettings
from .....dto.agent import AgentConversation, AgentReply
from .....services.agent.runtime import AgentRuntime
from .....services.agent.service import AgentService
from .....services.ports.agent import AgentMemoryPort
from ....texts.agent import AGENT_EXPIRED, render_agent_reply
from .formatting import format_agent_text
from .routing import AgentMessageFilter
from .typing import agent_typing_status

_TELEGRAM_TEXT_LIMIT = 4000


class AgentConfirmation(CallbackData, prefix="agent"):
    confirmation_id: str
    approved: bool


def create_agent_router(settings: AppSettings, memory: AgentMemoryPort) -> Router:
    """Build an opt-in router after existing handlers and dialog setup."""
    router = Router(name="agent")
    router.message.register(
        handle_agent_message, AgentMessageFilter(settings, memory), flags={"rate_limit": "expensive"}
    )
    router.callback_query.register(handle_agent_confirmation, AgentConfirmation.filter())
    return router


async def handle_agent_message(
    message: Message,
    service: FromDishka[AgentService],
    runtime: FromDishka[AgentRuntime],
    memory: FromDishka[AgentMemoryPort],
    bot: Bot,
) -> None:
    if message.from_user is None or message.text is None:
        return
    logger.info("event=agent_run status=started chat_type={chat_type}", chat_type=message.chat.type)
    conversation = AgentConversation(
        chat_id=message.chat.id, topic_id=message.message_thread_id, telegram_id=message.from_user.id
    )
    async with runtime.for_user(message.from_user.id):
        try:
            has_registered_user = await service.has_registered_user(conversation)
        except Exception:
            has_registered_user = False
        if has_registered_user:
            async with agent_typing_status(bot, message.chat.id, message.message_thread_id):
                result = await service.answer(
                    conversation, message.text, is_forwarded=message.forward_origin is not None
                )
        else:
            result = await service.answer(conversation, message.text, is_forwarded=message.forward_origin is not None)
        await _send_reply(bot, message, result, runtime, memory)


async def handle_agent_confirmation(  # noqa: PLR0913
    callback: CallbackQuery,
    callback_data: AgentConfirmation,
    service: FromDishka[AgentService],
    runtime: FromDishka[AgentRuntime],
    memory: FromDishka[AgentMemoryPort],
    bot: Bot,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer(AGENT_EXPIRED, show_alert=True)
        return
    conversation = AgentConversation(
        chat_id=message.chat.id, topic_id=message.message_thread_id, telegram_id=callback.from_user.id
    )
    async with runtime.for_user(callback.from_user.id):
        if callback_data.approved:
            async with agent_typing_status(bot, message.chat.id, message.message_thread_id):
                result = await service.confirm(
                    callback_data.confirmation_id, conversation, message.message_id, approved=True
                )
        else:
            result = await service.confirm(
                callback_data.confirmation_id, conversation, message.message_id, approved=False
            )
        if result.alert:
            await callback.answer(render_agent_reply(result), show_alert=True)
            return
        await callback.answer()
        await message.edit_reply_markup(reply_markup=None)
        # The confirmation message replies to the original user's question.
        target = (message.reply_to_message or message).model_copy(
            update={"message_thread_id": message.message_thread_id}
        )
        try:
            await _send_reply(bot, target, result, runtime, memory)
        except Exception as error:
            logger.warning(
                "event=agent_confirmation phase=result_delivery status=failed error_type={error_type}",
                error_type=type(error).__name__,
            )


async def _send_reply(
    bot: Bot, message: Message, result: AgentReply, runtime: AgentRuntime, memory: AgentMemoryPort
) -> None:
    keyboard = None
    if result.confirmation_id is not None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=AgentConfirmation(
                            confirmation_id=result.confirmation_id, approved=approved
                        ).pack(),
                    )
                    for label, approved in (("Да", True), ("Отмена", False))
                ]
            ]
        )
    text = format_agent_text(render_agent_reply(result))
    chunk_starts = range(0, max(len(text), 1), _TELEGRAM_TEXT_LIMIT)
    for chunk_index, start in enumerate(chunk_starts):
        is_last_chunk = chunk_index == len(chunk_starts) - 1
        try:
            sent = await bot.send_message(
                chat_id=message.chat.id,
                text=text[start : start + _TELEGRAM_TEXT_LIMIT] or "Попробуйте уточнить запрос",
                message_thread_id=message.message_thread_id,
                parse_mode=None,
                business_connection_id=message.business_connection_id,
                reply_parameters=ReplyParameters(message_id=message.message_id),
                reply_markup=keyboard if is_last_chunk else None,
            )
        except Exception:
            if result.confirmation_id is not None:
                runtime.invalidate(result.confirmation_id)
            raise
        if result.confirmation_id is not None and is_last_chunk:
            runtime.bind_message(result.confirmation_id, sent.message_id)
        await memory.mark_reply(message.chat.id, message.message_thread_id, sent.message_id)
