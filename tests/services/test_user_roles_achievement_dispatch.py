from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

from pybot.core.constants import RoleEnum
from pybot.db.models import Role, User
from pybot.services.ports import CommunityAchievementDispatchPort
from pybot.services.user_services.user_roles import UserRolesService


class FailingAchievementDispatch(CommunityAchievementDispatchPort):
    def __init__(self) -> None:
        self.user_ids: list[int] = []

    async def recalculate_user_achievements(self, user_id: int) -> None:
        self.user_ids.append(user_id)
        raise RuntimeError("TaskIQ is unavailable")


@pytest.mark.asyncio
async def test_mentor_assignment_queues_achievement_without_failing_role_change() -> None:
    db = Mock()
    db.commit = AsyncMock()
    user = User(
        id=17,
        first_name="Участник",
        last_name="Тестовый",
        telegram_id=771234,
        join_date=date(2026, 1, 1),
        academic_points=0,
        reputation_points=0,
        roles=[],
    )
    user_repository = Mock()
    user_repository.get_by_telegram_id = AsyncMock(return_value=user)
    user_repository.has_role = AsyncMock(return_value=False)
    role_repository = Mock()
    role_repository.find_role_by_name = AsyncMock(return_value=Role(id=3, name="Mentor"))
    dispatch = FailingAchievementDispatch()
    service = UserRolesService(
        db=db,
        user_repository=user_repository,
        role_repository=role_repository,
        achievement_dispatch_port=dispatch,
    )

    result = await service.add_user_role(telegram_id=771234, new_role=RoleEnum.MENTOR)

    assert result.id == user.id
    db.commit.assert_awaited_once()
    assert dispatch.user_ids == [user.id]
