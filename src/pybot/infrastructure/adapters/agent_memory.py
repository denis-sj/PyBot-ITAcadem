"""Redis text memory with atomic trimming and TTL renewal."""

from redis.asyncio import Redis

from ...core.config import AppSettings
from ...dto.agent import AgentConversation, AgentMemoryMessage

_MESSAGE_MAX_LENGTH = 2000


class RedisAgentMemory:
    """Keep tool results out of persisted conversation history."""

    def __init__(self, redis: Redis, settings: AppSettings) -> None:
        self.redis = redis
        self.settings = settings

    async def load(self, conversation: AgentConversation) -> list[AgentMemoryMessage]:
        """Load text-only messages from Redis."""
        values = await self.redis.execute_command("LRANGE", conversation.memory_key, 0, -1)
        return [AgentMemoryMessage.model_validate_json(value) for value in values]

    async def append(self, conversation: AgentConversation, question: str, answer: str) -> None:
        """Atomically append, trim and renew TTL after a completed turn."""
        messages = (
            AgentMemoryMessage(role="user", text=question[:_MESSAGE_MAX_LENGTH]),
            AgentMemoryMessage(role="assistant", text=answer[:_MESSAGE_MAX_LENGTH]),
        )
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.rpush(conversation.memory_key, *(message.model_dump_json() for message in messages))
            pipeline.ltrim(conversation.memory_key, -self.settings.agent_memory_max_messages, -1)
            pipeline.expire(conversation.memory_key, self.settings.agent_memory_ttl_s)
            await pipeline.execute()

    async def mark_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> None:
        """Keep provenance independently of expiring text so old replies start new turns."""
        await self.redis.set(self._reply_key(chat_id, topic_id, message_id), "1")

    async def is_agent_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> bool:
        """Distinguish agent replies from regular bot messages."""
        return bool(await self.redis.exists(self._reply_key(chat_id, topic_id, message_id)))

    @staticmethod
    def _reply_key(chat_id: int, topic_id: int | None, message_id: int) -> str:
        return f"agent:reply:{chat_id}:{topic_id or 0}:{message_id}"
