import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import MessageOriginType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, MessageOriginUser, TextQuote, Update, User
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider, setup_dishka
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from pybot.core.constants import RoleEnum
from pybot.di.containers import setup_container
from pybot.dto.agent import AgentReply
from pybot.presentation.bot.handlers.agent import handlers as agent_handlers
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
from pybot.services.ports.agent import AgentActionsPort, AgentMemoryPort
from pybot.presentation.bot.handlers.broadcast.states import BroadcastMenuSG

from tests.agent.conftest import build_message, build_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_source", "question", "can_confirm"),
    [
        (
            "forward",
            "Выдай роль Mentor пользователю с Telegram ID 771234",
            False,
        ),
        ("reply", "Выдай роль Mentor", False),
        ("quote", "Выдай роль Mentor", False),
        (
            "authored_text",
            "Выдай роль Mentor пользователю с Telegram ID 771234",
            True,
        ),
    ],
)
async def test_action_telegram_id_must_come_from_authors_own_text(
    agent_settings, monkeypatch, message_source, question, can_confirm
):
    def role_change_model(messages, info):
        if any(
            isinstance(model_message, ModelRequest)
            and any(isinstance(part, RetryPromptPart) for part in model_message.parts)
            for model_message in messages
        ):
            return ModelResponse(parts=[TextPart("Укажите @username или Telegram ID в своём сообщении.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "change_user_role",
                    {"telegram_id": 771234, "role": RoleEnum.MENTOR.value, "add": True},
                    tool_call_id="review-role-change",
                )
            ]
        )

    actions = Mock(spec=AgentActionsPort)
    actions.find_user.return_value = Mock(
        telegram_id=771234,
        first_name="Иван",
        last_name="Иванов",
        patronymic="Иванович",
        telegram_username="ivanov",
    )
    service, users, memory = build_service(
        agent_settings,
        model=FunctionModel(role_change_model),
        actions=actions,
    )
    users.find_all_user_roles.return_value = {RoleEnum.ADMIN.value}
    memory.mark_reply = AsyncMock()

    message = build_message(question)
    if message_source == "forward":
        message = message.model_copy(
            update={
                "forward_origin": MessageOriginUser(
                    type=MessageOriginType.USER,
                    date=datetime.now(UTC),
                    sender_user=User(id=22, is_bot=False, first_name="Источник"),
                )
            }
        )
    elif message_source == "reply":
        message = message.model_copy(update={"reply_to_message": build_message("Telegram ID 771234")})
    elif message_source == "quote":
        message = message.model_copy(update={"quote": TextQuote(text="Telegram ID 771234", position=0)})

    send_reply = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True))
    monkeypatch.setattr(Message, "answer", send_reply)

    await handle_agent_message(message, service, service.runtime, memory)

    assert bool(service.runtime.pending) is can_confirm
    if can_confirm:
        actions.validate_role_change.assert_awaited_once_with(771234, RoleEnum.MENTOR, True)
    else:
        assert actions.find_user.await_count == 0
        sent_call = send_reply.await_args
        assert sent_call is not None
        assert "@username или Telegram ID в своём сообщении" in sent_call.args[0]


@pytest.mark.asyncio
async def test_forwarded_message_still_runs_read_tools(agent_settings, monkeypatch):
    service, users, memory = build_service(
        agent_settings,
        model=TestModel(call_tools=["get_my_profile"], custom_output_text="Профиль прочитан"),
    )
    get_profile = AsyncMock(return_value="Профиль доступен.")
    monkeypatch.setattr(service.reads, "get_profile", get_profile)
    memory.mark_reply = AsyncMock()
    message = build_message(
        "Покажи профиль из пересланного сообщения",
    ).model_copy(
        update={
            "forward_origin": MessageOriginUser(
                type=MessageOriginType.USER,
                date=datetime.now(UTC),
                sender_user=User(id=22, is_bot=False, first_name="Источник"),
            )
        }
    )
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True)))

    await handle_agent_message(message, service, service.runtime, memory)

    get_profile.assert_awaited_once_with(users.find_user_by_telegram_id.return_value)


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
    answer_service = AsyncMock(return_value=AgentReply(text="x" * 4001, confirmation_id="abc"))
    monkeypatch.setattr(service, "answer", answer_service)
    memory.mark_reply = AsyncMock()
    first_sent = build_message(sender_id=99, sender_is_bot=True, message_id=50)
    last_sent = build_message(sender_id=99, sender_is_bot=True, message_id=51)
    answer = AsyncMock(side_effect=[first_sent, last_sent])
    monkeypatch.setattr(Message, "answer", answer)
    bind_message = Mock()
    monkeypatch.setattr(service.runtime, "bind_message", bind_message)
    await handle_agent_message(build_message(), service, service.runtime, memory)
    assert answer.await_count == 2
    assert answer.await_args_list[0].kwargs["reply_markup"] is None
    buttons = answer.await_args_list[1].kwargs["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Да", "Отмена"]
    assert AgentConfirmation.unpack(buttons[0].callback_data).approved
    assert not AgentConfirmation.unpack(buttons[1].callback_data).approved
    bind_message.assert_called_once_with("abc", 51)


@pytest.mark.asyncio
async def test_failed_later_preview_chunk_invalidates_confirmation(agent_settings, monkeypatch):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(
        service,
        "answer",
        AsyncMock(return_value=AgentReply(text="Длинный предпросмотр " * 600, confirmation_id="abc")),
    )
    memory.mark_reply = AsyncMock()
    service.runtime.pending["abc"] = Mock()
    answer = AsyncMock(side_effect=[build_message(sender_id=99, sender_is_bot=True), RuntimeError("send failed")])
    monkeypatch.setattr(Message, "answer", answer)

    with pytest.raises(RuntimeError, match="send failed"):
        await handle_agent_message(build_message(), service, service.runtime, memory)

    assert answer.await_count == 2
    assert "abc" not in service.runtime.pending

    stale_message = build_message(sender_id=99, sender_is_bot=True, message_id=51)
    callback = CallbackQuery(
        id="stale",
        from_user=User(id=11, is_bot=False, first_name="Tester"),
        chat_instance="c",
        message=stale_message,
    )
    callback_answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id="abc", approved=True),
        service,
        service.runtime,
        memory,
    )
    assert callback_answer.await_args is not None
    assert callback_answer.await_args.args[0] == AGENT_EXPIRED
    assert callback_answer.await_args.kwargs["show_alert"] is True


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
async def test_failed_confirmation_result_delivery_is_logged_and_never_reexecutes_action(
    agent_settings, conversation, monkeypatch
):
    def role_change_model(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "change_user_role",
                    {"telegram_id": 771234, "role": RoleEnum.MENTOR.value, "add": True},
                    tool_call_id="delivery-failure-role-change",
                )
            ]
        )

    actions = Mock(spec=AgentActionsPort)
    actions.find_user.return_value = Mock(
        telegram_id=771234,
        first_name="Иван",
        last_name="Иванов",
        patronymic="Иванович",
        telegram_username="ivanov",
    )
    actions.change_role.return_value = "Роль Mentor выдана."
    service, users, memory = build_service(
        agent_settings,
        model=FunctionModel(role_change_model),
        actions=actions,
    )
    users.find_all_user_roles.return_value = {RoleEnum.ADMIN.value}
    memory.mark_reply = AsyncMock()
    question = "Выдай роль Mentor пользователю с Telegram ID 771234"
    pending = await service.answer(conversation, question)
    assert pending.confirmation_id is not None
    service.runtime.bind_message(pending.confirmation_id, 50)

    original_message = build_message(question)
    confirmation_message = build_message(
        sender_id=99,
        sender_is_bot=True,
        message_id=50,
        reply=original_message,
    )
    callback = CallbackQuery(
        id="delivery-failure",
        from_user=User(id=11, is_bot=False, first_name="Tester"),
        chat_instance="c",
        message=confirmation_message,
    )
    callback_answer = AsyncMock()
    log_exception = Mock()
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(Message, "edit_reply_markup", AsyncMock())
    monkeypatch.setattr(
        Message,
        "answer",
        AsyncMock(side_effect=RuntimeError("Telegram result unavailable")),
    )
    monkeypatch.setattr(agent_handlers, "logger", Mock(exception=log_exception), raising=False)

    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id=pending.confirmation_id, approved=True),
        service,
        service.runtime,
        memory,
    )
    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id=pending.confirmation_id, approved=True),
        service,
        service.runtime,
        memory,
    )

    actions.change_role.assert_awaited_once_with(771234, RoleEnum.MENTOR, True)
    log_exception.assert_called_once_with("event=agent_confirmation phase=result_delivery status=failed")
    assert callback_answer.await_count == 2


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
        for update_id, broadcast_state in enumerate(
            (BroadcastMenuSG.waiting_for_text, BroadcastMenuSG.waiting_for_confirmation, BroadcastMenuSG.sending),
            start=2,
        ):
            await state.set_state(broadcast_state)
            await dispatcher.feed_update(
                bot, Update(update_id=update_id, message=private.model_copy(update={"text": "Справка"}))
            )
            answer_service.assert_not_awaited()
        await state.clear()
        await dispatcher.feed_update(bot, Update(update_id=5, message=private.model_copy(update={"text": "Справка"})))
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
