"""Thin Telegram entrypoints for questions and author-owned confirmations."""

from aiogram import Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from dishka import FromDishka

from .....core.config import AppSettings
from .....dto.agent import AgentConversation, AgentReply
from .....services.agent.runtime import AgentRuntime
from .....services.agent.service import AgentService
from .....services.ports.agent import AgentMemoryPort
from ....texts.agent import AGENT_EXPIRED, render_agent_reply
from .routing import AgentMessageFilter

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
) -> None:
    if message.from_user is None or message.text is None:
        return
    conversation = AgentConversation(
        chat_id=message.chat.id, topic_id=message.message_thread_id, telegram_id=message.from_user.id
    )
    async with runtime.for_user(message.from_user.id):
        result = await service.answer(conversation, message.text)
        await _send_reply(message, result, runtime, memory)


async def handle_agent_confirmation(
    callback: CallbackQuery,
    callback_data: AgentConfirmation,
    service: FromDishka[AgentService],
    runtime: FromDishka[AgentRuntime],
    memory: FromDishka[AgentMemoryPort],
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer(AGENT_EXPIRED, show_alert=True)
        return
    conversation = AgentConversation(
        chat_id=message.chat.id, topic_id=message.message_thread_id, telegram_id=callback.from_user.id
    )
    async with runtime.for_user(callback.from_user.id):
        result = await service.confirm(
            callback_data.confirmation_id, conversation, message.message_id, approved=callback_data.approved
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
        await _send_reply(target, result, runtime, memory)


async def _send_reply(message: Message, result: AgentReply, runtime: AgentRuntime, memory: AgentMemoryPort) -> None:
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
    text = render_agent_reply(result)
    for start in range(0, max(len(text), 1), _TELEGRAM_TEXT_LIMIT):
        sent = await message.answer(
            text[start : start + _TELEGRAM_TEXT_LIMIT] or "Попробуйте уточнить запрос",
            parse_mode=None,
            message_thread_id=message.message_thread_id,
            reply_parameters=ReplyParameters(message_id=message.message_id),
            reply_markup=keyboard if start == 0 else None,
        )
        if result.confirmation_id is not None and start == 0:
            runtime.bind_message(result.confirmation_id, sent.message_id)
        await memory.mark_reply(message.chat.id, message.message_thread_id, sent.message_id)
