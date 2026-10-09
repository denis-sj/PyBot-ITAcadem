from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Chat, Message, User
from pydantic_ai import models
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from pybot.core.config import AppSettings
from pybot.dto.agent import AgentConversation
from pybot.services.agent.runtime import AgentRuntime
from pybot.services.agent.service import AgentService
from pybot.services.agent.tools import AgentToolRegistry
from pybot.services.ports.agent import AgentMemoryPort, AgentModelPort
from pybot.services.user_services.users import UserService


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    yield


@pytest.fixture
def conversation() -> AgentConversation:
    return AgentConversation(chat_id=-100, topic_id=3, telegram_id=11)


@pytest.fixture
def agent_settings(settings_obj: AppSettings) -> AppSettings:
    settings_obj.agent_enabled = True
    settings_obj.agent_group_chat_id = -100
    settings_obj.agent_topic_id = 3
    return settings_obj


def build_message(  # noqa: PLR0913
    text: str | None = "Что умеет бот?",
    *,
    chat_type: str = "supergroup",
    chat_id: int = -100,
    topic_id: int | None = 3,
    sender_id: int = 11,
    sender_is_bot: bool = False,
    reply: Message | None = None,
    message_id: int = 1,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        message_thread_id=topic_id,
        is_topic_message=topic_id is not None,
        from_user=User(id=sender_id, is_bot=sender_is_bot, first_name="Tester"),
        text=text,
        reply_to_message=reply,
    )


def build_service(
    settings: AppSettings,
    *,
    model: Model | None = None,
    registry: AgentToolRegistry | None = None,
) -> tuple[AgentService, Mock, Mock]:
    users = Mock(spec=UserService)
    users.find_user_by_telegram_id = AsyncMock(return_value=Mock(id=42))
    users.find_all_user_roles = AsyncMock(return_value={"Student"})
    memory = Mock(spec=AgentMemoryPort)
    memory.load = AsyncMock(return_value=[])
    memory.append = AsyncMock()
    model_port = Mock(spec=AgentModelPort)
    model_port.get_model.return_value = model or TestModel(custom_output_text="Откройте /profile или /visits")
    runtime = AgentRuntime()
    service = AgentService(users, model_port, memory, runtime, registry or AgentToolRegistry(), settings)
    return service, users, memory
