import pytest
from fakeredis import FakeAsyncRedis

from pybot.dto.agent import AgentConversation
from pybot.infrastructure.adapters.agent_memory import RedisAgentMemory


@pytest.mark.asyncio
async def test_memory_is_bounded_truncated_and_has_renewed_ttl(agent_settings, conversation):
    async with FakeAsyncRedis(decode_responses=True) as redis:
        memory = RedisAgentMemory(redis, agent_settings)
        for index in range(8):
            await memory.append(conversation, f"question-{index}" + "я" * 2200, f"answer-{index}" + "а" * 2200)
        messages = await memory.load(conversation)
        assert len(messages) == 10
        assert messages[0].text.startswith("question-3")
        assert messages[-1].text.startswith("answer-7")
        assert all(len(message.text) == 2000 for message in messages)
        assert [message.role for message in messages] == ["user", "assistant"] * 5
        ttl = await redis.ttl(conversation.memory_key)
        assert 1799 <= ttl <= 1800
        await redis.expire(conversation.memory_key, 1)
        await memory.append(conversation, "new question", "new answer")
        assert await redis.ttl(conversation.memory_key) >= 1799
        await redis.expire(conversation.memory_key, 0)
        assert await memory.load(conversation) == []


@pytest.mark.asyncio
async def test_conversations_are_separated_by_chat_topic_and_user(agent_settings, conversation):
    async with FakeAsyncRedis(decode_responses=True) as redis:
        memory = RedisAgentMemory(redis, agent_settings)
        keys = [
            conversation,
            conversation.model_copy(update={"chat_id": -200}),
            conversation.model_copy(update={"topic_id": 4}),
            conversation.model_copy(update={"telegram_id": 12}),
            conversation.model_copy(update={"topic_id": None}),
        ]
        assert len({key.memory_key for key in keys}) == len(keys)
        for index, key in enumerate(keys):
            await memory.append(key, f"q-{index}", f"a-{index}")
        for index, key in enumerate(keys):
            assert [message.text for message in await memory.load(key)] == [f"q-{index}", f"a-{index}"]


@pytest.mark.asyncio
async def test_reply_provenance_survives_memory_expiration(agent_settings, conversation):
    async with FakeAsyncRedis(decode_responses=True) as redis:
        memory = RedisAgentMemory(redis, agent_settings)
        await memory.append(conversation, "question", "answer")
        await memory.mark_reply(-100, 3, 55)
        assert 604799 <= await redis.ttl("agent:reply:-100:3:55") <= 604800
        await redis.expire(conversation.memory_key, 0)
        assert await memory.load(conversation) == []
        assert await memory.is_agent_reply(-100, 3, 55)
        assert not await memory.is_agent_reply(-200, 3, 55)
        assert not await memory.is_agent_reply(-100, 4, 55)
        assert not await memory.is_agent_reply(-100, 3, 56)
