"""Immutable contracts for agent conversations and presentation results."""

from enum import StrEnum
from typing import Literal

from .base_dto import BaseDTO


class AgentConversation(BaseDTO):
    chat_id: int
    topic_id: int | None = None
    telegram_id: int

    @property
    def memory_key(self) -> str:
        """Return a conversation key isolated by chat, topic and author."""
        return f"agent:memory:{self.chat_id}:{self.topic_id or 0}:{self.telegram_id}"


class AgentMemoryMessage(BaseDTO):
    role: Literal["user", "assistant"]
    text: str


class AgentReplyStatus(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    REGISTRATION_REQUIRED = "registration_required"
    ACCESS_DENIED = "access_denied"
    EXPIRED = "expired"
    NOT_AUTHOR = "not_author"
    CANCELLED = "cancelled"


class AgentReply(BaseDTO):
    text: str = ""
    status: AgentReplyStatus = AgentReplyStatus.SUCCESS
    confirmation_id: str | None = None
    alert: bool = False
