"""Ports for agent model construction and text-only conversation storage."""

from typing import Protocol

from pydantic_ai.models import Model

from ...dto.agent import AgentConversation, AgentMemoryMessage


class AgentModelPort(Protocol):
    """Construct the configured model without making network requests."""

    def get_model(self) -> Model:
        """Return an OpenAI-compatible model."""
        ...


class AgentMemoryPort(Protocol):
    """Store only user questions and final answers, never tool traces."""

    async def load(self, conversation: AgentConversation) -> list[AgentMemoryMessage]:
        """Load the bounded text history."""
        ...

    async def append(self, conversation: AgentConversation, question: str, answer: str) -> None:
        """Append a turn and renew its TTL."""
        ...

    async def mark_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> None:
        """Record a message sent by the agent."""
        ...

    async def is_agent_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> bool:
        """Check provenance independently of conversation memory expiration."""
        ...
