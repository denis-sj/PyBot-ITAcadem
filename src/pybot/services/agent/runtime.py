"""Process-local sequencing and expiring deferred approvals for one bot replica."""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage

from ...dto.agent import AgentConversation


@dataclass
class PendingAgentRequest:
    """A suspended run lives only in this process and expires on restart."""

    conversation: AgentConversation
    question: str
    messages: list[ModelMessage]
    requests: DeferredToolRequests
    usage: RunUsage
    expires_at: float
    message_id: int | None = None


@dataclass
class _UserQueue:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class AgentRuntime:
    """Share queues and pending requests across Dishka request scopes."""

    def __init__(self) -> None:
        self.queues: dict[int, _UserQueue] = {}
        self.pending: dict[str, PendingAgentRequest] = {}

    @asynccontextmanager
    async def for_user(self, telegram_id: int) -> AsyncIterator[None]:
        """Serialize a user's entire turn, including Telegram response delivery."""
        queue = self.queues.setdefault(telegram_id, _UserQueue())
        queue.users += 1
        try:
            async with queue.lock:
                yield
        finally:
            queue.users -= 1
            if queue.users == 0:
                del self.queues[telegram_id]

    def defer(self, request: PendingAgentRequest) -> str:
        """Remove expired requests and register a fresh unguessable confirmation."""
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value.expires_at > now}
        confirmation_id = secrets.token_hex(12)
        self.pending[confirmation_id] = request
        return confirmation_id

    def bind_message(self, confirmation_id: str, message_id: int) -> None:
        """Bind confirmation to the exact message carrying its keyboard."""
        request = self.pending.get(confirmation_id)
        if request is not None:
            request.message_id = message_id

    def find_pending(self, confirmation_id: str) -> PendingAgentRequest | None:
        """Find a live request; an expired request is removed immediately."""
        request = self.pending.get(confirmation_id)
        if request is not None and request.expires_at <= time.monotonic():
            self.pending.pop(confirmation_id, None)
            return None
        return request
