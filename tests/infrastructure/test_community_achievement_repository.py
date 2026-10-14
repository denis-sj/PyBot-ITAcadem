from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.db.models import User, UserAchievementAward
from pybot.infrastructure.community_achievement_repository import CommunityAchievementRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_award_code_is_unique_per_user_and_preserves_first_award(db_session: AsyncSession) -> None:
    user = User(id=551122, first_name="Участник", telegram_id=551122)
    db_session.add(user)
    await db_session.commit()
    repository = CommunityAchievementRepository()
    first_earned_at = datetime(2026, 10, 14, 10, tzinfo=UTC)

    first_count = await repository.add_missing_awards(
        db_session,
        user_id=user.id,
        awards={"first_step": first_earned_at},
    )
    await db_session.commit()
    duplicate_count = await repository.add_missing_awards(
        db_session,
        user_id=user.id,
        awards={"first_step": datetime(2026, 10, 15, 10, tzinfo=UTC)},
    )
    await db_session.commit()
    result = await db_session.execute(
        select(UserAchievementAward).where(
            UserAchievementAward.user_id == user.id,
            UserAchievementAward.code == "first_step",
        )
    )
    rows = result.scalars().all()

    assert first_count == 1
    assert duplicate_count == 0
    assert len(rows) == 1
    assert rows[0].achieved_at == first_earned_at
