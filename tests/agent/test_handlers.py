import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update, User
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider, setup_dishka

from pybot.di.containers import setup_container
from pybot.dto.agent import AgentReply
from pybot.presentation.bot.handlers.agent.handlers import (
    AgentConfirmation,
    create_agent_router,
    handle_agent_confirmation,
    handle_agent_message,
)
from pybot.presentation.bot.tg_bot_run import setup_agent_handler
from pybot.services.agent.runtime import AgentRuntime
from pybot.services.agent.service import AgentService
from pybot.presentation.texts.agent import AGENT_EXPIRED
from pybot.services.ports.agent import AgentMemoryPort
from tests.agent.conftest import build_message, build_service


@pytest.mark.asyncio
async def test_answer_keeps_chat_topic_reply_plain_text_and_queue(agent_settings, monkeypatch):
    service, _, memory = build_service(agent_settings)
    runtime = service.runtime
    memory.mark_reply = AsyncMock()
    sent = build_message("Откройте /profile", sender_id=99, sender_is_bot=True, message_id=50)

    async def send(*args, **kwargs):
        assert runtime.queues[11].lock.locked()
        return sent

    answer = AsyncMock(side_effect=send)
    monkeypatch.setattr(Message, "answer", answer)
    await handle_agent_message(build_message(), service, runtime, memory)
    assert answer.await_args is not None
    kwargs = answer.await_args.kwargs
    assert kwargs["parse_mode"] is None
    assert kwargs["message_thread_id"] == 3
    assert kwargs["reply_parameters"].message_id == 1
    memory.mark_reply.assert_awaited_once_with(-100, 3, 50)
    assert runtime.queues == {}


@pytest.mark.asyncio
async def test_confirmation_keyboard_and_message_binding(agent_settings, monkeypatch):
    service, _, memory = build_service(agent_settings)
    answer_service = AsyncMock(return_value=AgentReply(text="Будет выполнено действие", confirmation_id="abc"))
    monkeypatch.setattr(service, "answer", answer_service)
    memory.mark_reply = AsyncMock()
    sent = build_message(sender_id=99, sender_is_bot=True, message_id=50)
    answer = AsyncMock(return_value=sent)
    monkeypatch.setattr(Message, "answer", answer)
    bind_message = Mock()
    monkeypatch.setattr(service.runtime, "bind_message", bind_message)
    await handle_agent_message(build_message(), service, service.runtime, memory)
    assert answer.await_args is not None
    buttons = answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Да", "Отмена"]
    assert AgentConfirmation.unpack(buttons[0].callback_data).approved
    assert not AgentConfirmation.unpack(buttons[1].callback_data).approved
    bind_message.assert_called_once_with("abc", 50)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [True, False])
async def test_callback_alert_does_not_remove_keyboard(agent_settings, monkeypatch, foreign):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(
        service,
        "confirm",
        AsyncMock(return_value=AgentReply(text="Это не ваш запрос" if foreign else AGENT_EXPIRED, alert=True)),
    )
    message = build_message(sender_id=99, sender_is_bot=True, message_id=50)
    callback = CallbackQuery(
        id="cb", from_user=User(id=12, is_bot=False, first_name="Tester"), chat_instance="c", message=message
    )
    answer = AsyncMock()
    edit = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(Message, "edit_reply_markup", edit)
    await handle_agent_confirmation(
        callback, AgentConfirmation(confirmation_id="abc", approved=True), service, service.runtime, memory
    )
    assert answer.await_args is not None
    assert answer.await_args.kwargs["show_alert"] is True
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_clears_keyboard_and_replies_to_original_question(agent_settings, monkeypatch):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(service, "confirm", AsyncMock(return_value=AgentReply(text="Отменено")))
    memory.mark_reply = AsyncMock()
    original = build_message(message_id=4, topic_id=None)
    confirmation = build_message(sender_id=99, sender_is_bot=True, message_id=50, reply=original)
    callback = CallbackQuery(
        id="cb", from_user=User(id=11, is_bot=False, first_name="Tester"), chat_instance="c", message=confirmation
    )
    answer = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=51))
    edit = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "edit_reply_markup", edit)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    await handle_agent_confirmation(
        callback, AgentConfirmation(confirmation_id="abc", approved=False), service, service.runtime, memory
    )
    edit.assert_awaited_once_with(reply_markup=None)
    assert answer.await_args is not None
    assert answer.await_args.kwargs["reply_parameters"].message_id == 4
    assert answer.await_args.kwargs["message_thread_id"] == 3


@pytest.mark.asyncio
async def test_real_dispatcher_command_priority_and_fsm(agent_settings, monkeypatch):
    service, _, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    memory.is_agent_reply = AsyncMock(return_value=False)
    answer_service = AsyncMock(return_value=AgentReply(text="Справка"))
    monkeypatch.setattr(service, "answer", answer_service)

    class Dependencies(Provider):
        @provide(scope=Scope.APP)
        def agent_service(self) -> AgentService:
            return service

        @provide(scope=Scope.APP)
        def agent_runtime(self) -> AgentRuntime:
            return service.runtime

        @provide(scope=Scope.APP)
        def agent_memory(self) -> AgentMemoryPort:
            return memory

    container = make_async_container(Dependencies(), AiogramProvider())
    dispatcher = Dispatcher()
    command_router = Router()
    handled_command = AsyncMock()

    async def command(message: Message) -> None:
        await handled_command(message)

    command_router.message.register(command, Command("profile"))
    dispatcher.include_router(command_router)
    dispatcher.include_router(create_agent_router(agent_settings, memory))
    setup_dishka(container, dispatcher, auto_inject=True)
    bot = Bot("99:test-token")
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=build_message(message_id=50)))
    try:
        await dispatcher.emit_startup(bot=bot)
        private = build_message("/profile", chat_type="private", chat_id=11, topic_id=None)
        await dispatcher.feed_update(bot, Update(update_id=1, message=private))
        handled_command.assert_awaited_once()
        answer_service.assert_not_awaited()
        state = dispatcher.fsm.get_context(bot, chat_id=11, user_id=11)
        await state.set_state("Broadcast:waiting_text")
        await dispatcher.feed_update(bot, Update(update_id=2, message=private.model_copy(update={"text": "Справка"})))
        answer_service.assert_not_awaited()
        await state.clear()
        await dispatcher.feed_update(bot, Update(update_id=3, message=private.model_copy(update={"text": "Справка"})))
        answer_service.assert_awaited_once()
        assert dispatcher.sub_routers[-1].message.handlers[0].flags["rate_limit"] == "expensive"
    finally:
        await dispatcher.storage.close()
        await container.close()
        await bot.session.close()


@pytest.mark.asyncio
async def test_public_di_resolves_shared_runtime_and_request_service(agent_settings):
    container = await setup_container()
    try:
        runtime = await container.get(AgentRuntime)
        async with container() as request:
            first = await request.get(AgentService)
        async with container() as request:
            second = await request.get(AgentService)
        assert first is not second
        assert first.runtime is second.runtime is runtime
        assert first.memory is second.memory
        assert first.model is second.model
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_disabled_agent_does_not_register_or_resolve_dependencies(agent_settings):
    agent_settings.agent_enabled = False
    container = Mock(get=AsyncMock())
    dispatcher = Dispatcher()
    await setup_agent_handler(dispatcher, container, agent_settings)
    container.get.assert_not_awaited()
    assert dispatcher.sub_routers == []
    await dispatcher.storage.close()
