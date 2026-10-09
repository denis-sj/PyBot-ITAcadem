from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from pybot.domain.exceptions import (
    RoleAlreadyAssignedError,
    RoleRequestCooldownError,
    RoleRequestNotAllowedError,
)
from pybot.presentation.bot import cmd_role_request
from pybot.presentation.texts import ROLE_REQUEST_ALREADY_ASSIGNED, ROLE_REQUEST_USAGE


def _build_message(
    *,
    text: str,
    from_user_id: int = 930_001,
    date: datetime | None = None,
) -> Message:
    sender = User(id=from_user_id, is_bot=False, first_name="Student")
    return Message(
        message_id=1,
        date=date or datetime.now(UTC),
        chat=Chat(id=from_user_id, type="private"),
        from_user=sender,
        text=text,
    )


@pytest.mark.asyncio
async def test_cmd_role_request_replies_with_cooldown_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available_at = datetime(2026, 3, 23, 14, 30, 0)
    role_request_service = AsyncMock()
    role_request_service.create_role_request.side_effect = RoleRequestCooldownError(
        user_id=1,
        role_name="Mentor",
        available_at=available_at,
    )

    message = _build_message(text="/role_request Mentor")
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)

    await cmd_role_request(
        message=message,
        role_request_service=role_request_service,
        user_id=1,
    )

    role_request_service.create_role_request.assert_awaited_once_with(1, "Mentor")
    reply_mock.assert_awaited_once_with("Повторный запрос можно отправить после 23.03.2026.")


@pytest.mark.asyncio
async def test_cmd_role_request_student_requests_mentor_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_request_service = AsyncMock()
    message = _build_message(text="/role_request Mentor")
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)

    await cmd_role_request(
        message=message,
        role_request_service=role_request_service,
        user_id=1,
    )

    role_request_service.create_role_request.assert_awaited_once_with(1, "Mentor")
    assert "отправлен администратору" in reply_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_role_request_rejects_disallowed_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_request_service = AsyncMock()
    role_request_service.create_role_request.side_effect = RoleRequestNotAllowedError()

    message = _build_message(text="/role_request Admin")
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)

    await cmd_role_request(
        message=message,
        role_request_service=role_request_service,
        user_id=1,
    )

    reply_mock.assert_awaited_once_with("Нет ролей, которые вы можете запросить.")


@pytest.mark.asyncio
async def test_cmd_role_request_already_has_mentor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_request_service = AsyncMock()
    role_request_service.create_role_request.side_effect = RoleAlreadyAssignedError(user_id=1, role_name="Mentor")

    message = _build_message(text="/role_request Mentor")
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)

    await cmd_role_request(
        message=message,
        role_request_service=role_request_service,
        user_id=1,
    )

    reply_mock.assert_awaited_once_with(ROLE_REQUEST_ALREADY_ASSIGNED)
    assert "У вас уже есть роль Mentor." in reply_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_role_request_explains_correct_format_for_unknown_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_request_service = AsyncMock()
    message = _build_message(text="/role_request Curator")
    reply_mock = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply_mock)

    await cmd_role_request(
        message=message,
        role_request_service=role_request_service,
        user_id=1,
    )

    role_request_service.create_role_request.assert_not_awaited()
    reply_mock.assert_awaited_once_with(ROLE_REQUEST_USAGE)
