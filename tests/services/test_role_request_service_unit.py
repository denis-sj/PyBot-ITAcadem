"""Unit tests for RoleRequestService business logic, restrictions, and auto-close."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pybot.core.config import AppSettings
from pybot.core.constants import RequestStatus
from pybot.db.models import Role, RoleRequest, User
from pybot.domain.exceptions import (
    RoleAlreadyAssignedError,
    RoleRequestNotAllowedError,
)
from pybot.infrastructure import RoleRepository, RoleRequestRepository, UserRepository
from pybot.services.ports import NotificationPort
from pybot.services.role_request import RoleRequestService


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def mock_role_repo() -> AsyncMock:
    repo = AsyncMock(spec=RoleRepository)
    mentor_role = Role(id=2, name="Mentor")
    admin_role = Role(id=3, name="Admin")
    repo.find_role_by_name.side_effect = lambda _db, name: (
        mentor_role if name == "Mentor" else admin_role if name == "Admin" else None
    )
    return repo


@pytest.fixture
def mock_user_repo() -> AsyncMock:
    return AsyncMock(spec=UserRepository)


@pytest.fixture
def mock_role_request_repo() -> AsyncMock:
    repo = AsyncMock(spec=RoleRequestRepository)
    repo.find_recent_active_request.return_value = None
    repo.find_last_rejected_request.return_value = None
    return repo


@pytest.fixture
def mock_notification_service() -> AsyncMock:
    return AsyncMock(spec=NotificationPort)


@pytest.fixture
def role_request_service(
    mock_db: AsyncMock,
    mock_role_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_role_request_repo: AsyncMock,
    mock_notification_service: AsyncMock,
    settings_obj: AppSettings,
) -> RoleRequestService:
    return RoleRequestService(
        db=mock_db,
        role_repository=mock_role_repo,
        user_repository=mock_user_repo,
        role_request_repository=mock_role_request_repo,
        notification_service=mock_notification_service,
        settings=settings_obj,
    )


@pytest.mark.asyncio
async def test_student_can_request_mentor_success(
    role_request_service: RoleRequestService,
    mock_user_repo: AsyncMock,
    mock_db: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    student_user = User(id=1, telegram_id=111, first_name="Студент")
    mock_user_repo.get_by_id.return_value = student_user
    mock_user_repo.find_user_roles.return_value = ["Student"]

    dto = await role_request_service.create_role_request(user_id=1, role="Mentor")

    assert dto.user_id == 1
    assert dto.role_id == 2
    assert dto.status == RequestStatus.PENDING
    mock_db.commit.assert_awaited_once()
    mock_notification_service.send_role_request_to_admin.assert_awaited_once()
    call_args = mock_notification_service.send_role_request_to_admin.call_args[0]
    assert call_args[1] == 111
    assert call_args[2] == "Mentor"


@pytest.mark.asyncio
async def test_student_requesting_admin_rejected(
    role_request_service: RoleRequestService,
    mock_user_repo: AsyncMock,
) -> None:
    student_user = User(id=1, telegram_id=111, first_name="Студент")
    mock_user_repo.get_by_id.return_value = student_user
    mock_user_repo.find_user_roles.return_value = ["Student"]

    with pytest.raises(RoleRequestNotAllowedError) as exc_info:
        await role_request_service.create_role_request(user_id=1, role="Admin")

    assert exc_info.value.message == "Нет ролей, которые вы можете запросить."


@pytest.mark.asyncio
async def test_admin_requesting_any_role_rejected(
    role_request_service: RoleRequestService,
    mock_user_repo: AsyncMock,
) -> None:
    admin_user = User(id=2, telegram_id=222, first_name="Админ")
    mock_user_repo.get_by_id.return_value = admin_user
    mock_user_repo.find_user_roles.return_value = ["Admin"]

    with pytest.raises(RoleRequestNotAllowedError) as exc_info:
        await role_request_service.create_role_request(user_id=2, role="Mentor")

    assert exc_info.value.message == "Нет ролей, которые вы можете запросить."


@pytest.mark.asyncio
async def test_mentor_requesting_admin_rejected(
    role_request_service: RoleRequestService,
    mock_user_repo: AsyncMock,
) -> None:
    mentor_user = User(id=3, telegram_id=333, first_name="Ментор")
    mock_user_repo.get_by_id.return_value = mentor_user
    mock_user_repo.find_user_roles.return_value = ["Mentor"]

    with pytest.raises(RoleRequestNotAllowedError) as exc_info:
        await role_request_service.create_role_request(user_id=3, role="Admin")

    assert exc_info.value.message == "Нет ролей, которые вы можете запросить."


@pytest.mark.asyncio
async def test_mentor_requesting_mentor_already_assigned(
    role_request_service: RoleRequestService,
    mock_user_repo: AsyncMock,
) -> None:
    mentor_user = User(id=3, telegram_id=333, first_name="Ментор")
    mock_user_repo.get_by_id.return_value = mentor_user
    mock_user_repo.find_user_roles.return_value = ["Mentor"]

    with pytest.raises(RoleAlreadyAssignedError):
        await role_request_service.create_role_request(user_id=3, role="Mentor")


@pytest.mark.asyncio
async def test_auto_close_pending_request_on_role_assignment(
    role_request_service: RoleRequestService,
    mock_role_request_repo: AsyncMock,
    mock_db: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    pending_request = RoleRequest(id=77, user_id=1, role_id=2, status=RequestStatus.PENDING)
    mock_role_request_repo.find_recent_active_request.return_value = pending_request

    closed = await role_request_service.auto_close_pending_request_on_role_assignment(
        user_id=1,
        role_name="Mentor",
    )

    assert closed is not None
    assert closed.status == RequestStatus.APPROVED
    mock_db.commit.assert_awaited_once()
    mock_notification_service.update_role_request_message.assert_awaited_once_with(
        request_id=77,
        status_text="Запрос закрыт: роль выдана командой /addrole.",
    )


@pytest.mark.asyncio
async def test_auto_close_when_no_pending_request(
    role_request_service: RoleRequestService,
    mock_role_request_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    mock_role_request_repo.find_recent_active_request.return_value = None

    closed = await role_request_service.auto_close_pending_request_on_role_assignment(
        user_id=1,
        role_name="Mentor",
    )

    assert closed is None
    mock_notification_service.update_role_request_message.assert_not_awaited()
