from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.types import User

from pybot.presentation.bot.handlers.agent.routing import AgentMessageFilter
from pybot.services.ports.agent import AgentMemoryPort
from tests.agent.conftest import build_message


@pytest.fixture
def router_filter(agent_settings):
    memory = Mock(spec=AgentMemoryPort)
    memory.is_agent_reply = AsyncMock(return_value=False)
    return AgentMessageFilter(agent_settings, memory), memory


@pytest.fixture
def bot():
    result = Mock(spec=Bot)
    result.id = 99
    result.me = AsyncMock(return_value=User(id=99, is_bot=True, first_name="Bot", username="academy_bot"))
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_type,chat_id,topic_id,text,expected",
    [
        ("private", 11, None, "Привет", True),
        ("private", 11, None, "/profile", False),
        ("supergroup", -100, 3, "Привет", True),
        ("supergroup", -100, 4, "Привет", False),
        ("supergroup", -200, 3, "Привет", False),
        ("supergroup", -100, 4, "@academy_bot помоги", True),
        ("supergroup", -100, 4, "@ACADEMY_BOT помоги", True),
        ("supergroup", -100, 3, "@someone привет", False),
        ("group", -200, None, "@academy_bot помоги", True),
        ("group", -200, None, "@academy_bot_fake привет", False),
        ("group", -200, None, "abc@academy_bot привет", False),
        ("supergroup", -100, 3, " /profile@academy_bot", False),
        ("supergroup", -100, 3, None, False),
        ("channel", -100, None, "@academy_bot помоги", False),
    ],
)
async def test_routing_matrix(router_filter, bot, chat_type, chat_id, topic_id, text, expected):
    agent_filter, _ = router_filter
    message = build_message(text, chat_type=chat_type, chat_id=chat_id, topic_id=topic_id)
    assert await agent_filter(message, bot) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,dialog", [("Broadcast:waiting_text", None), (None, Mock()), ("Registration:name", Mock())]
)
async def test_active_fsm_and_dialog_take_priority(router_filter, bot, state, dialog):
    agent_filter, _ = router_filter
    assert not await agent_filter(build_message("@academy_bot help"), bot, raw_state=state, aiogd_context=dialog)
    bot.me.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("sender,marked,expected", [(99, True, True), (99, False, False), (12, True, False)])
async def test_reply_requires_agent_provenance(router_filter, bot, sender, marked, expected):
    agent_filter, memory = router_filter
    memory.is_agent_reply.return_value = marked
    reply = build_message(sender_id=sender, sender_is_bot=sender == 99, message_id=50)
    assert await agent_filter(build_message(topic_id=4, reply=reply), bot) is expected
    if sender == 99:
        memory.is_agent_reply.assert_awaited_once_with(-100, 4, 50)


@pytest.mark.asyncio
async def test_disabled_and_bot_messages_never_run(router_filter, bot):
    agent_filter, _ = router_filter
    assert not await agent_filter(build_message(sender_is_bot=True), bot)
    agent_filter.settings.agent_enabled = False
    assert not await agent_filter(build_message(), bot)


@pytest.mark.asyncio
async def test_photo_and_anonymous_sender_do_not_run(router_filter, bot):
    agent_filter, _ = router_filter
    photo = build_message(None).model_copy(update={"photo": [Mock()]})
    assert not await agent_filter(photo, bot)
    assert not await agent_filter(build_message().model_copy(update={"from_user": None}), bot)
    assert not await agent_filter(build_message().model_copy(update={"sender_chat": Mock()}), bot)
