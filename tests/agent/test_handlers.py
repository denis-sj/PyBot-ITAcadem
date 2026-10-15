import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import MessageOriginType
from aiogram.filters import Command
from aiogram.methods import SendChatAction, SendMessage
from aiogram.types import CallbackQuery, Message, MessageOriginUser, TextQuote, Update, User
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import AiogramProvider, setup_dishka
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from pybot.core.constants import RoleEnum
from pybot.di.containers import setup_container
from pybot.dto.agent import AgentReply
from pybot.presentation.bot import tg_bot_run
from pybot.presentation.bot.handlers.agent import handlers as agent_handlers
from pybot.presentation.bot.handlers.agent import typing as agent_typing
from pybot.presentation.bot.handlers.agent.handlers import (
    AgentConfirmation,
    create_agent_router,
    handle_agent_confirmation,
    handle_agent_message,
)
from pybot.presentation.bot.handlers.broadcast.states import BroadcastMenuSG
from pybot.presentation.bot.tg_bot_run import setup_agent_handler
from pybot.presentation.texts.agent import AGENT_EXPIRED
from pybot.services.agent.runtime import AgentRuntime
from pybot.services.agent.service import AgentService
from pybot.services.ports.agent import AgentActionsPort, AgentMemoryPort
from tests.agent.conftest import build_message, build_service


def patch_telegram_api(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    methods: list[object] = []

    async def make_request(request_bot: Bot, method: object, timeout: int | None = None) -> object:
        del timeout
        methods.append(method)
        if isinstance(method, SendMessage):
            return build_message(
                text=method.text,
                chat_type="private" if int(method.chat_id) > 0 else "supergroup",
                chat_id=int(method.chat_id),
                topic_id=method.message_thread_id,
                sender_id=request_bot.id,
                sender_is_bot=True,
                message_id=50 + len(methods),
            ).as_(request_bot)
        return True

    monkeypatch.setattr(bot.session, "make_request", make_request)
    return methods


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
    agent_settings, agent_bot, message_source, question, can_confirm
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
    agent_bot.send_message = send_reply

    await handle_agent_message(message, service, service.runtime, memory, bot=agent_bot)

    assert bool(service.runtime.pending) is can_confirm
    if can_confirm:
        actions.validate_role_change.assert_awaited_once_with(771234, RoleEnum.MENTOR, True)
    else:
        assert actions.find_user.await_count == 0
        sent_call = send_reply.await_args
        assert sent_call is not None
        assert "@username или Telegram ID в своём сообщении" in sent_call.kwargs["text"]


@pytest.mark.asyncio
async def test_forwarded_message_still_runs_read_tools(agent_settings, monkeypatch, agent_bot):
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
    await handle_agent_message(message, service, service.runtime, memory, bot=agent_bot)

    get_profile.assert_awaited_once_with(users.find_user_by_telegram_id.return_value)


@pytest.mark.asyncio
async def test_answer_keeps_chat_topic_reply_plain_text_and_queue(agent_settings, agent_bot):
    service, _, memory = build_service(agent_settings)
    runtime = service.runtime
    memory.mark_reply = AsyncMock()
    sent = build_message("Откройте /profile", sender_id=99, sender_is_bot=True, message_id=50)

    async def send(*args, **kwargs):
        assert runtime.queues[11].lock.locked()
        return sent

    answer = AsyncMock(side_effect=send)
    agent_bot.send_message = answer
    await handle_agent_message(build_message(), service, runtime, memory, bot=agent_bot)
    assert answer.await_args is not None
    kwargs = answer.await_args.kwargs
    assert kwargs["parse_mode"] is None
    assert kwargs["message_thread_id"] == 3
    assert kwargs["reply_parameters"].message_id == 1
    memory.mark_reply.assert_awaited_once_with(-100, 3, 50)
    assert runtime.queues == {}


@pytest.mark.asyncio
async def test_agent_tool_names_and_goal_confirmation_are_sent_as_plain_text(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    hostile_text = 'Помощник: <b>x</b> и <a href="tg://user?id=1">Аня</a>. Записал твою новую цель: «<b>x</b>».'
    monkeypatch.setattr(service, "answer", AsyncMock(return_value=AgentReply(text=hostile_text)))
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))

    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    sent_message = agent_bot.send_message.await_args
    assert sent_message is not None
    assert sent_message.kwargs["text"] == hostile_text
    assert sent_message.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_accepted_registered_message_shows_typing_in_its_topic_and_stops_after_reply(
    agent_settings, agent_bot, monkeypatch
):
    service, users, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    action_started = asyncio.Event()
    answer_started = asyncio.Event()
    finish_answer = asyncio.Event()

    async def send_chat_action(**kwargs):
        action_started.set()

    async def answer(*args, **kwargs):
        answer_started.set()
        await finish_answer.wait()
        return AgentReply(text="Готово")

    agent_bot.send_chat_action = AsyncMock(side_effect=send_chat_action)
    monkeypatch.setattr(service, "answer", answer)
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))

    task = asyncio.create_task(handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot))
    await asyncio.wait_for(answer_started.wait(), timeout=1)
    await asyncio.wait_for(action_started.wait(), timeout=1)
    finish_answer.set()
    await task
    await asyncio.sleep(0)

    users.find_user_by_telegram_id.assert_awaited_once()
    agent_bot.send_chat_action.assert_awaited_once_with(chat_id=-100, action="typing", message_thread_id=3)
    assert not any(task.get_name() == "agent-typing-status" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_unregistered_message_does_not_show_typing(agent_settings, agent_bot):
    service, users, memory = build_service(agent_settings)
    users.find_user_by_telegram_id.return_value = None
    memory.mark_reply = AsyncMock()
    agent_bot.send_chat_action = AsyncMock()

    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    assert agent_bot.send_chat_action.await_count == 0


@pytest.mark.asyncio
async def test_typing_stops_when_agent_raises(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    action_started = asyncio.Event()

    async def send_chat_action(**kwargs):
        action_started.set()

    async def fail_after_typing(*args, **kwargs):
        await action_started.wait()
        raise RuntimeError("private failure")

    agent_bot.send_chat_action = AsyncMock(side_effect=send_chat_action)
    monkeypatch.setattr(service, "answer", fail_after_typing)

    with pytest.raises(RuntimeError, match="private failure"):
        await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    assert agent_bot.send_chat_action.await_count == 1
    await asyncio.sleep(0)
    assert agent_bot.send_chat_action.await_count == 1
    assert not any(task.get_name() == "agent-typing-status" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_typing_repeats_during_long_agent_run(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    action_started = asyncio.Event()

    async def send_chat_action(**kwargs):
        action_started.set()

    async def answer(*args, **kwargs):
        await action_started.wait()
        await asyncio.sleep(0.035)
        return AgentReply(text="Готово")

    monkeypatch.setattr(agent_typing, "_TYPING_INTERVAL_S", 0.01)
    monkeypatch.setattr(service, "answer", answer)
    agent_bot.send_chat_action = AsyncMock(side_effect=send_chat_action)
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))

    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    assert agent_bot.send_chat_action.await_count >= 2


@pytest.mark.asyncio
async def test_typing_api_failure_does_not_break_agent_reply(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    agent_bot.send_chat_action = AsyncMock(side_effect=RuntimeError("Telegram private failure"))
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))

    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    agent_bot.send_chat_action.assert_awaited()
    agent_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_confirmation_shows_typing_until_action_finishes(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    action_started = asyncio.Event()
    finish_action = asyncio.Event()
    callback_message = build_message(sender_id=99, sender_is_bot=True, message_id=50)
    callback = CallbackQuery(
        id="confirm-typing",
        from_user=User(id=11, is_bot=False, first_name="Tester"),
        chat_instance="c",
        message=callback_message,
    )

    async def send_chat_action(**kwargs):
        action_started.set()

    async def confirm(*args, **kwargs):
        await action_started.wait()
        await finish_action.wait()
        return AgentReply(text="Действие выполнено")

    agent_bot.send_chat_action = AsyncMock(side_effect=send_chat_action)
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=51))
    monkeypatch.setattr(service, "confirm", confirm)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(Message, "edit_reply_markup", AsyncMock())

    task = asyncio.create_task(
        handle_agent_confirmation(
            callback,
            AgentConfirmation(confirmation_id="confirm-typing", approved=True),
            service,
            service.runtime,
            memory,
            bot=agent_bot,
        )
    )
    await asyncio.wait_for(action_started.wait(), timeout=1)
    finish_action.set()
    await task
    await asyncio.sleep(0)

    agent_bot.send_chat_action.assert_awaited_once_with(chat_id=-100, action="typing", message_thread_id=3)
    assert not any(task.get_name() == "agent-typing-status" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_agent_answer_is_cleaned_before_sending(agent_settings, agent_bot, monkeypatch):
    service, _, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    monkeypatch.setattr(
        service,
        "answer",
        AsyncMock(return_value=AgentReply(text="Привет, **ITAcadem**!\n\n### 📋 Для всех\n- **Профиль** — `/profile`")),
    )
    agent_bot.send_message = AsyncMock(return_value=build_message(sender_id=99, sender_is_bot=True, message_id=50))

    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

    send_call = agent_bot.send_message.await_args
    assert send_call is not None
    sent_text = send_call.kwargs["text"]
    assert sent_text == "Привет, ITAcadem!\n\n📋 Для всех\n• Профиль — /profile"
    assert "**" not in sent_text
    assert "###" not in sent_text
    assert "`" not in sent_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "chat_id", "topic_id"),
    [
        ("private", 11, None),
        ("group", -22, None),
        ("supergroup", -100, 3),
    ],
)
async def test_real_aiogram_message_sends_reply_to_private_group_and_topic(
    agent_settings, monkeypatch, chat_type, chat_id, topic_id
):
    bot = Bot("99:test-token")
    service, _, memory = build_service(agent_settings)
    memory.mark_reply = AsyncMock()
    message = build_message(chat_type=chat_type, chat_id=chat_id, topic_id=topic_id).as_(bot)
    methods = patch_telegram_api(bot, monkeypatch)

    try:
        await handle_agent_message(message, service, service.runtime, memory, bot=bot)
    finally:
        await bot.session.close()

    send = next(method for method in methods if isinstance(method, SendMessage))
    assert send.chat_id == chat_id
    assert send.message_thread_id == topic_id
    assert send.reply_parameters is not None
    assert send.reply_parameters.message_id == message.message_id
    assert send.parse_mode is None
    memory.mark_reply.assert_awaited_once_with(chat_id, topic_id, 52)


@pytest.mark.asyncio
async def test_confirmation_sends_real_aiogram_reply_to_original_message_in_topic(agent_settings, monkeypatch):
    bot = Bot("99:test-token")
    service, _, memory = build_service(agent_settings)
    confirm = AsyncMock(return_value=AgentReply(text="Роль обновлена."))
    monkeypatch.setattr(service, "confirm", confirm)
    memory.mark_reply = AsyncMock()
    original = build_message(message_id=4, chat_type="supergroup", chat_id=-100, topic_id=3)
    confirmation = build_message(
        sender_id=99,
        sender_is_bot=True,
        message_id=50,
        chat_type="supergroup",
        chat_id=-100,
        topic_id=3,
        reply=original,
    ).as_(bot)
    callback = CallbackQuery(
        id="real-callback",
        from_user=User(id=11, is_bot=False, first_name="Tester"),
        chat_instance="c",
        message=confirmation,
    ).as_(bot)
    methods = patch_telegram_api(bot, monkeypatch)

    try:
        await handle_agent_confirmation(
            callback,
            AgentConfirmation(confirmation_id="confirm-1", approved=True),
            service,
            service.runtime,
            memory,
            bot=bot,
        )
    finally:
        await bot.session.close()

    send = next(method for method in methods if isinstance(method, SendMessage))
    assert send.chat_id == -100
    assert send.message_thread_id == 3
    assert send.reply_parameters is not None
    assert send.reply_parameters.message_id == original.message_id
    assert memory.mark_reply.await_args is not None
    assert memory.mark_reply.await_args.args == (-100, 3, 53)


@pytest.mark.asyncio
async def test_agent_run_started_log_contains_chat_type_without_question_text(agent_settings, monkeypatch, agent_bot):
    service, _, memory = build_service(agent_settings)
    log = Mock()
    monkeypatch.setattr(agent_handlers, "logger", log)
    await handle_agent_message(build_message("PRIVATE_SENTINEL"), service, service.runtime, memory, bot=agent_bot)

    log.info.assert_called_once_with("event=agent_run status=started chat_type={chat_type}", chat_type="supergroup")
    assert "PRIVATE_SENTINEL" not in repr(log.info.call_args)


@pytest.mark.asyncio
async def test_confirmation_keyboard_and_message_binding(agent_settings, monkeypatch, agent_bot):
    service, _, memory = build_service(agent_settings)
    answer_service = AsyncMock(return_value=AgentReply(text="x" * 4001, confirmation_id="abc"))
    monkeypatch.setattr(service, "answer", answer_service)
    memory.mark_reply = AsyncMock()
    first_sent = build_message(sender_id=99, sender_is_bot=True, message_id=50)
    last_sent = build_message(sender_id=99, sender_is_bot=True, message_id=51)
    answer = AsyncMock(side_effect=[first_sent, last_sent])
    agent_bot.send_message = answer
    bind_message = Mock()
    monkeypatch.setattr(service.runtime, "bind_message", bind_message)
    await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)
    assert answer.await_count == 2
    assert answer.await_args_list[0].kwargs["reply_markup"] is None
    buttons = answer.await_args_list[1].kwargs["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Да", "Отмена"]
    assert AgentConfirmation.unpack(buttons[0].callback_data).approved
    assert not AgentConfirmation.unpack(buttons[1].callback_data).approved
    bind_message.assert_called_once_with("abc", 51)


@pytest.mark.asyncio
async def test_failed_later_preview_chunk_invalidates_confirmation(agent_settings, monkeypatch, agent_bot):
    service, _, memory = build_service(agent_settings)
    monkeypatch.setattr(
        service,
        "answer",
        AsyncMock(return_value=AgentReply(text="Длинный предпросмотр " * 600, confirmation_id="abc")),
    )
    memory.mark_reply = AsyncMock()
    service.runtime.pending["abc"] = Mock()
    answer = AsyncMock(side_effect=[build_message(sender_id=99, sender_is_bot=True), RuntimeError("send failed")])
    agent_bot.send_message = answer

    with pytest.raises(RuntimeError, match="send failed"):
        await handle_agent_message(build_message(), service, service.runtime, memory, bot=agent_bot)

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
        bot=agent_bot,
    )
    assert callback_answer.await_args is not None
    assert callback_answer.await_args.args[0] == AGENT_EXPIRED
    assert callback_answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [True, False])
async def test_callback_alert_does_not_remove_keyboard(agent_settings, monkeypatch, foreign, agent_bot):
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
        callback,
        AgentConfirmation(confirmation_id="abc", approved=True),
        service,
        service.runtime,
        memory,
        bot=agent_bot,
    )
    assert answer.await_args is not None
    assert answer.await_args.kwargs["show_alert"] is True
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_clears_keyboard_and_replies_to_original_question(agent_settings, monkeypatch, agent_bot):
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
    agent_bot.send_message = answer
    monkeypatch.setattr(Message, "edit_reply_markup", edit)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id="abc", approved=False),
        service,
        service.runtime,
        memory,
        bot=agent_bot,
    )
    edit.assert_awaited_once_with(reply_markup=None)
    assert answer.await_args is not None
    assert answer.await_args.kwargs["reply_parameters"].message_id == 4
    assert answer.await_args.kwargs["message_thread_id"] == 3


@pytest.mark.asyncio
async def test_failed_confirmation_result_delivery_is_logged_and_never_reexecutes_action(
    agent_settings, conversation, monkeypatch, agent_bot
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
    log_warning = Mock()
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(Message, "edit_reply_markup", AsyncMock())
    agent_bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram result unavailable"))
    monkeypatch.setattr(agent_handlers, "logger", Mock(warning=log_warning), raising=False)

    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id=pending.confirmation_id, approved=True),
        service,
        service.runtime,
        memory,
        bot=agent_bot,
    )
    await handle_agent_confirmation(
        callback,
        AgentConfirmation(confirmation_id=pending.confirmation_id, approved=True),
        service,
        service.runtime,
        memory,
        bot=agent_bot,
    )

    actions.change_role.assert_awaited_once_with(771234, RoleEnum.MENTOR, True)
    log_warning.assert_called_once_with(
        "event=agent_confirmation phase=result_delivery status=failed error_type={error_type}",
        error_type="RuntimeError",
    )
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
    methods = patch_telegram_api(bot, monkeypatch)
    start_log = Mock()
    monkeypatch.setattr(agent_handlers, "logger", start_log)
    try:
        await dispatcher.emit_startup(bot=bot)
        private = build_message("/profile", chat_type="private", chat_id=11, topic_id=None)
        await dispatcher.feed_update(bot, Update(update_id=1, message=private))
        handled_command.assert_awaited_once()
        answer_service.assert_not_awaited()
        assert not any(isinstance(method, SendChatAction) for method in methods)
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
        assert not any(isinstance(method, SendChatAction) for method in methods)
        await state.clear()
        await dispatcher.feed_update(bot, Update(update_id=5, message=private.model_copy(update={"text": "Справка"})))
        answer_service.assert_awaited_once()
        start_log.info.assert_called_once_with(
            "event=agent_run status=started chat_type={chat_type}", chat_type="private"
        )
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
@pytest.mark.parametrize(
    ("enabled", "group_chat_id", "topic_id", "expected_status", "expected_topic"),
    [
        (True, -100, 3, "enabled", "true"),
        (False, None, None, "disabled", "false"),
    ],
)
async def test_agent_setup_logs_enabled_status_and_topic(
    agent_settings,
    monkeypatch,
    enabled,
    group_chat_id,
    topic_id,
    expected_status,
    expected_topic,
):
    log = Mock()
    monkeypatch.setattr(tg_bot_run, "logger", log)
    agent_settings.agent_enabled = enabled
    agent_settings.agent_model = "test/agent-model"
    agent_settings.agent_group_chat_id = group_chat_id
    agent_settings.agent_topic_id = topic_id
    container = Mock(get=AsyncMock(return_value=Mock(spec=AgentMemoryPort)))
    dispatcher = Dispatcher()

    try:
        await setup_agent_handler(dispatcher, container, agent_settings)
    finally:
        await dispatcher.storage.close()

    log.info.assert_called_once_with(
        "event=agent_setup status={status} model={model} topic_configured={topic_configured}",
        status=expected_status,
        model="test/agent-model",
        topic_configured=expected_topic,
    )
    if enabled:
        container.get.assert_awaited_once_with(AgentMemoryPort)
        assert len(dispatcher.sub_routers) == 1
    else:
        container.get.assert_not_awaited()
        assert dispatcher.sub_routers == []


@pytest.mark.asyncio
async def test_disabled_agent_does_not_register_or_resolve_dependencies(agent_settings):
    agent_settings.agent_enabled = False
    container = Mock(get=AsyncMock())
    dispatcher = Dispatcher()
    await setup_agent_handler(dispatcher, container, agent_settings)
    container.get.assert_not_awaited()
    assert dispatcher.sub_routers == []
    await dispatcher.storage.close()
