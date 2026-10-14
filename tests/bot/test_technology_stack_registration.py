from datetime import UTC, datetime
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from aiogram_dialog import DialogManager

from pybot.domain.services.technology_stack import TechnologyLevel
from pybot.dto.technology_stack import TechnologyStackItemDTO
from pybot.presentation.bot.dialogs.user_reg.handlers import (
    _on_technology_stack_edit_impl,
    _on_technology_stack_input_impl,
    _on_technology_stack_save_impl,
    _on_technology_stack_skip_impl,
)
from pybot.services.technology_stack import TechnologyStackService
from pybot.services.technology_stack_parsing import TechnologyStackParsingService


def _message(text: str, *, telegram_id: int = 100_001) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_id, type="private"),
        from_user=User(id=telegram_id, is_bot=False, first_name="Участник"),
        text=text,
    )


@dataclass
class ManagerTestState:
    manager: DialogManager
    dialog_data: dict[str, object]
    next_mock: AsyncMock
    back_mock: AsyncMock
    done_mock: AsyncMock


@dataclass
class CallbackTestState:
    callback: CallbackQuery
    answer_mock: AsyncMock


def _manager(*, owner_telegram_id: int = 100_001) -> ManagerTestState:
    manager_data: dict[str, object] = {
        "registered_user_id": 1,
        "stack_owner_telegram_id": owner_telegram_id,
        "technology_stack_saved": False,
    }
    next_mock = AsyncMock()
    back_mock = AsyncMock()
    done_mock = AsyncMock()
    manager = cast(
        "DialogManager",
        SimpleNamespace(
            dialog_data=manager_data,
            next=next_mock,
            back=back_mock,
            done=done_mock,
        ),
    )
    return ManagerTestState(manager, manager_data, next_mock, back_mock, done_mock)


def _callback(telegram_id: int, message: Message | None = None) -> CallbackTestState:
    answer_mock = AsyncMock()
    callback = cast(
        "CallbackQuery",
        SimpleNamespace(
            from_user=User(id=telegram_id, is_bot=False, first_name="Участник"),
            message=message,
            answer=answer_mock,
        ),
    )
    return CallbackTestState(callback, answer_mock)


@pytest.mark.asyncio
async def test_stack_message_creates_preview_without_persisting(mocker) -> None:
    manager_state = _manager()
    parsing = mocker.create_autospec(TechnologyStackParsingService, instance=True)
    parsing.parse = AsyncMock(
        return_value=(
            TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="Docker", level=TechnologyLevel.LEARNING),
        )
    )

    await _on_technology_stack_input_impl(_message("Знаю питон, изучаю докер"), manager_state.manager, parsing)

    parsing.parse.assert_awaited_once_with("Знаю питон, изучаю докер")
    assert manager_state.dialog_data["technology_stack_draft"] == [
        {"technology": "Python", "level": "know"},
        {"technology": "Docker", "level": "learning"},
    ]
    assert manager_state.dialog_data["technology_stack_confirmation"] == "Записал: знаю — Python; изучаю — Docker."
    manager_state.next_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_in_stack_state_is_not_sent_to_parser(mocker) -> None:
    manager_state = _manager()
    parsing = mocker.create_autospec(TechnologyStackParsingService, instance=True)

    await _on_technology_stack_input_impl(_message("/profile"), manager_state.manager, parsing)

    parsing.parse.assert_not_awaited()
    manager_state.next_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_discards_stack_draft_without_saving() -> None:
    manager_state = _manager()
    manager_state.dialog_data["technology_stack_draft"] = [{"technology": "Python", "level": "know"}]
    callback_state = _callback(100_001)

    await _on_technology_stack_skip_impl(callback_state.callback, manager_state.manager)

    assert "technology_stack_draft" not in manager_state.dialog_data
    callback_state.answer_mock.assert_awaited_once()
    manager_state.done_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_user_cannot_save_registration_stack(mocker) -> None:
    manager_state = _manager()
    manager_state.dialog_data["technology_stack_draft"] = [{"technology": "Python", "level": "know"}]
    callback_state = _callback(200_002)
    service = mocker.create_autospec(TechnologyStackService, instance=True)

    await _on_technology_stack_save_impl(callback_state.callback, manager_state.manager, service)

    callback_state.answer_mock.assert_awaited_once()
    assert callback_state.answer_mock.await_args is not None
    assert callback_state.answer_mock.await_args.kwargs["show_alert"] is True
    service.replace_user_stack.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_discards_preview_and_returns_to_stack_prompt() -> None:
    manager_state = _manager()
    manager_state.dialog_data["technology_stack_draft"] = [{"technology": "Python", "level": "know"}]
    manager_state.dialog_data["technology_stack_confirmation"] = "черновик"
    callback_state = _callback(100_001)

    await _on_technology_stack_edit_impl(callback_state.callback, manager_state.manager)

    assert "technology_stack_draft" not in manager_state.dialog_data
    assert "technology_stack_confirmation" not in manager_state.dialog_data
    manager_state.back_mock.assert_awaited_once()
    callback_state.answer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_replaces_stack_once_and_repeated_click_is_ignored(mocker) -> None:
    manager_state = _manager()
    manager_state.dialog_data["technology_stack_draft"] = [
        {"technology": "Python", "level": "know"},
        {"technology": "Docker", "level": "learning"},
    ]
    callback_message = _message("preview")
    callback_state = _callback(100_001, callback_message)
    service = mocker.create_autospec(TechnologyStackService, instance=True)
    service.replace_user_stack = AsyncMock(
        return_value=(
            TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="Docker", level=TechnologyLevel.LEARNING),
        )
    )
    service.recommend_for_learning_items = AsyncMock(return_value=())
    edit_text = mocker.patch.object(Message, "edit_text", new=AsyncMock())

    await _on_technology_stack_save_impl(callback_state.callback, manager_state.manager, service)
    await _on_technology_stack_save_impl(callback_state.callback, manager_state.manager, service)

    service.replace_user_stack.assert_awaited_once_with(
        1,
        (
            TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="Docker", level=TechnologyLevel.LEARNING),
        ),
    )
    service.recommend_for_learning_items.assert_awaited_once()
    edit_text.assert_awaited_once()
    assert manager_state.dialog_data["technology_stack_saved"] is True
    assert manager_state.done_mock.await_count == 1
    assert callback_state.answer_mock.await_count == 2
