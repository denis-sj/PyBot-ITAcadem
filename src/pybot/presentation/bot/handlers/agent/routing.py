"""Last-resort text routing; existing commands and FSM/dialog flows win."""

import re

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram_dialog.api.entities import Context

from .....core import logger
from .....core.config import AppSettings
from .....services.ports.agent import AgentMemoryPort


class AgentMessageFilter(Filter):
    """Accept addressed text only when no other interaction owns the message."""

    def __init__(self, settings: AppSettings, memory: AgentMemoryPort) -> None:
        self.settings = settings
        self.memory = memory

    async def __call__(
        self,
        message: Message,
        bot: Bot,
        raw_state: str | None = None,
        aiogd_context: Context | None = None,
    ) -> bool:
        if not self.settings.agent_enabled or not message.text:
            return False
        skip_reason = self._interaction_skip_reason(message, raw_state, aiogd_context)
        if skip_reason is not None:
            self._log_skipped(skip_reason)
            return False
        if message.from_user is None or message.from_user.is_bot or message.sender_chat is not None:
            return False
        if message.chat.type == ChatType.PRIVATE:
            return True
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return False
        return await self._has_group_address(message, bot)

    @staticmethod
    def _interaction_skip_reason(message: Message, raw_state: str | None, aiogd_context: Context | None) -> str | None:
        if message.text is not None and message.text.lstrip().startswith("/"):
            return "command"
        if raw_state is not None:
            return "fsm_state"
        if aiogd_context is not None:
            return "dialog"
        return None

    async def _has_group_address(self, message: Message, bot: Bot) -> bool:
        if (
            message.chat.id == self.settings.agent_group_chat_id
            and message.message_thread_id == self.settings.agent_topic_id
        ):
            return True
        if message.text is not None and "@" in message.text:
            identity = await bot.me()
            if identity.username and re.search(
                rf"(?<!\w)@{re.escape(identity.username)}(?!\w)", message.text, re.IGNORECASE
            ):
                return True
        reply = message.reply_to_message
        if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
            self._log_skipped("no_group_address")
            return False
        if not await self.memory.is_agent_reply(message.chat.id, message.message_thread_id, reply.message_id):
            self._log_skipped("no_group_address")
            return False
        return True

    @staticmethod
    def _log_skipped(reason: str) -> None:
        logger.debug("event=agent_filter status=skipped reason={reason}", reason=reason)
