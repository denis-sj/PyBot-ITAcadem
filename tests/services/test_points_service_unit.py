"""Unit tests for PointsService business logic and rules."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from pybot.core.constants import PointsTypeEnum
from pybot.db.models import Level, User, UserLevel
from pybot.domain.exceptions import InsufficientPointsBalanceError
from pybot.domain.services.level_calculator import LevelCalculator
from pybot.dto import AdjustUserPointsDTO
from pybot.dto.value_objects import Points
from pybot.infrastructure.level_repository import LevelRepository
from pybot.infrastructure.points_transaction_repository import PointsTransactionRepository
from pybot.infrastructure.user_repository import UserRepository
from pybot.services.points import PointsService


def _make_test_user(
    user_id: int,
    academic_points: int = 0,
    reputation_points: int = 0,
) -> User:
    return User(
        id=user_id,
        telegram_id=user_id * 1000,
        first_name=f"User{user_id}",
        last_name=f"Last{user_id}",
        join_date=date(2026, 1, 1),
        academic_points=academic_points,
        reputation_points=reputation_points,
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def mock_user_repo() -> AsyncMock:
    return AsyncMock(spec=UserRepository)


@pytest.fixture
def mock_level_repo() -> AsyncMock:
    return AsyncMock(spec=LevelRepository)


@pytest.fixture
def mock_points_tx_repo() -> AsyncMock:
    return AsyncMock(spec=PointsTransactionRepository)


@pytest.fixture
def level_calculator() -> LevelCalculator:
    return LevelCalculator()


@pytest.fixture
def points_service(
    mock_db: AsyncMock,
    level_calculator: LevelCalculator,
    mock_user_repo: AsyncMock,
    mock_level_repo: AsyncMock,
    mock_points_tx_repo: AsyncMock,
) -> PointsService:
    return PointsService(
        db=mock_db,
        level_calculator=level_calculator,
        user_repository=mock_user_repo,
        level_repository=mock_level_repo,
        points_transaction_repository=mock_points_tx_repo,
    )


@pytest.mark.asyncio
async def test_deduct_academic_points_exceeding_balance_raises(
    points_service: PointsService,
    mock_db: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    recipient = _make_test_user(user_id=1, academic_points=5, reputation_points=10)
    giver = _make_test_user(user_id=2)
    mock_user_repo.get_by_id.side_effect = lambda _db, uid: recipient if uid == 1 else giver

    dto = AdjustUserPointsDTO(
        recipient_id=1,
        giver_id=2,
        points=Points(value=-10, point_type=PointsTypeEnum.ACADEMIC),
        reason="Penalty",
    )

    with pytest.raises(InsufficientPointsBalanceError) as exc_info:
        await points_service.change_points(dto)

    assert exc_info.value.requested == 10
    assert exc_info.value.current == 5
    assert exc_info.value.message == "Нельзя списать 10 баллов: у участника 5."

    # User balance was not altered and no commit happened
    assert recipient.academic_points == 5
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deduct_reputation_points_exceeding_balance_raises(
    points_service: PointsService,
    mock_db: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    recipient = _make_test_user(user_id=1, academic_points=50, reputation_points=3)
    giver = _make_test_user(user_id=2)
    mock_user_repo.get_by_id.side_effect = lambda _db, uid: recipient if uid == 1 else giver

    dto = AdjustUserPointsDTO(
        recipient_id=1,
        giver_id=2,
        points=Points(value=-7, point_type=PointsTypeEnum.REPUTATION),
        reason="Penalty",
    )

    with pytest.raises(InsufficientPointsBalanceError) as exc_info:
        await points_service.change_points(dto)

    assert exc_info.value.requested == 7
    assert exc_info.value.current == 3
    assert exc_info.value.message == "Нельзя списать 7 баллов: у участника 3."

    assert recipient.reputation_points == 3
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deduct_points_within_balance_does_not_decrease_level(
    points_service: PointsService,
    mock_db: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_level_repo: AsyncMock,
) -> None:
    level_basic = Level(id=1, name="A0", level_type=PointsTypeEnum.ACADEMIC, required_points=0)
    level_high = Level(id=2, name="A1", level_type=PointsTypeEnum.ACADEMIC, required_points=50)

    user_level_link = UserLevel(level_id=2)
    user_level_link.level = level_high

    recipient = _make_test_user(user_id=1, academic_points=60, reputation_points=0)
    recipient.user_levels = [user_level_link]
    giver = _make_test_user(user_id=2)

    mock_user_repo.get_by_id.side_effect = lambda _db, uid: recipient if uid == 1 else giver
    mock_level_repo.find_all_by_type.return_value = [level_basic, level_high]

    dto = AdjustUserPointsDTO(
        recipient_id=1,
        giver_id=2,
        points=Points(value=-20, point_type=PointsTypeEnum.ACADEMIC),
        reason="Partial deduction",
    )

    result = await points_service.change_points(dto)

    # Balance decreased to 40 (< 50 required for A1)
    assert result.academic_points.value == 40
    # But level remained level 2 (A1) because achieved levels are non-decreasing!
    assert recipient.user_levels[0].level_id == 2
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_points_increases_level_when_threshold_crossed(
    points_service: PointsService,
    mock_db: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_level_repo: AsyncMock,
) -> None:
    level_basic = Level(id=1, name="A0", level_type=PointsTypeEnum.ACADEMIC, required_points=0)
    level_high = Level(id=2, name="A1", level_type=PointsTypeEnum.ACADEMIC, required_points=50)

    user_level_link = UserLevel(level_id=1)
    user_level_link.level = level_basic

    recipient = _make_test_user(user_id=1, academic_points=40, reputation_points=0)
    recipient.user_levels = [user_level_link]
    giver = _make_test_user(user_id=2)

    mock_user_repo.get_by_id.side_effect = lambda _db, uid: recipient if uid == 1 else giver
    mock_level_repo.find_all_by_type.return_value = [level_basic, level_high]

    dto = AdjustUserPointsDTO(
        recipient_id=1,
        giver_id=2,
        points=Points(value=20, point_type=PointsTypeEnum.ACADEMIC),
        reason="Good work",
    )

    result = await points_service.change_points(dto)

    # Balance increased to 60 (>= 50 required for A1)
    assert result.academic_points.value == 60
    # Level upgraded to A1 (level_id=2)
    assert recipient.user_levels[0].level_id == 2
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_points_does_not_change_level_when_threshold_not_crossed(
    points_service: PointsService,
    mock_db: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_level_repo: AsyncMock,
) -> None:
    level_basic = Level(id=1, name="A0", level_type=PointsTypeEnum.ACADEMIC, required_points=0)
    level_high = Level(id=2, name="A1", level_type=PointsTypeEnum.ACADEMIC, required_points=50)

    user_level_link = UserLevel(level_id=1)
    user_level_link.level = level_basic

    recipient = _make_test_user(user_id=1, academic_points=10, reputation_points=0)
    recipient.user_levels = [user_level_link]
    giver = _make_test_user(user_id=2)

    mock_user_repo.get_by_id.side_effect = lambda _db, uid: recipient if uid == 1 else giver
    mock_level_repo.find_all_by_type.return_value = [level_basic, level_high]

    dto = AdjustUserPointsDTO(
        recipient_id=1,
        giver_id=2,
        points=Points(value=10, point_type=PointsTypeEnum.ACADEMIC),
        reason="Small bonus",
    )

    result = await points_service.change_points(dto)

    # Balance increased to 20 (< 50 required for A1)
    assert result.academic_points.value == 20
    # Level remained A0 (level_id=1)
    assert recipient.user_levels[0].level_id == 1
    mock_db.commit.assert_awaited_once()
