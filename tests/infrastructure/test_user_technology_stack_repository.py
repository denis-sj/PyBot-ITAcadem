from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.db.models import (
    AttendanceRecord,
    Competence,
    Role,
    User,
    UserCompetence,
    UserRole,
    UserTechnologyStack,
)
from pybot.domain.services.technology_stack import TechnologyLevel, TechnologyStackItem
from pybot.infrastructure.user_technology_stack_repository import UserTechnologyStackRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stack_rows_are_unique_and_one_item_updates_without_replacing_others(db_session: AsyncSession) -> None:
    user = User(id=8_883_020, first_name="Участник", telegram_id=8_883_020)
    db_session.add(user)
    await db_session.commit()
    repository = UserTechnologyStackRepository()

    await repository.replace_user_stack(
        db_session,
        user_id=user.id,
        items=(
            TechnologyStackItem("Docker", TechnologyLevel.LEARNING),
            TechnologyStackItem("Python", TechnologyLevel.KNOW),
        ),
    )
    await db_session.commit()
    first_rows = await repository.find_user_stack(db_session, user.id)
    first_updated_at = next(item.updated_at for item in first_rows if item.technology == "Docker")
    assert {(item.technology, item.level) for item in first_rows} == {
        ("Docker", TechnologyLevel.LEARNING),
        ("Python", TechnologyLevel.KNOW),
    }

    inserted = await repository.set_user_stack_item(
        db_session,
        user_id=user.id,
        item=TechnologyStackItem("Docker", TechnologyLevel.KNOW),
    )
    await db_session.commit()
    rows = await db_session.execute(select(UserTechnologyStack).where(UserTechnologyStack.user_id == user.id))
    stored_rows = rows.scalars().all()

    assert inserted is False
    assert len(stored_rows) == 2
    assert {item.technology: item.level for item in stored_rows} == {"Docker": "know", "Python": "know"}
    assert next(item.updated_at for item in stored_rows if item.technology == "Docker") >= first_updated_at

    assert await repository.remove_user_stack_item(db_session, user_id=user.id, technology="Python") is True
    await db_session.commit()
    remaining = await repository.find_user_stack(db_session, user.id)
    assert [(item.technology, item.level) for item in remaining] == [("Docker", TechnologyLevel.KNOW)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_helper_queries_match_competence_aliases_and_exclude_blocked_users(db_session: AsyncSession) -> None:
    requester = User(id=8_883_100, first_name="Запросивший", telegram_id=8_883_100)
    mentor = User(id=8_883_101, first_name="Наставник", telegram_id=8_883_101, telegram_username="mentor")
    blocked_mentor = User(
        id=8_883_102,
        first_name="Блокированный наставник",
        telegram_id=8_883_102,
        bot_is_blocked=True,
    )
    peer = User(id=8_883_103, first_name="Знающий участник", telegram_id=8_883_103)
    blocked_peer = User(id=8_883_104, first_name="Блокированный участник", telegram_id=8_883_104, bot_is_blocked=True)
    mentor_role = Role(id=8_883_100, name="Mentor")
    postgres_competence = Competence(id=8_883_100, name="постгрес")
    db_session.add_all([requester, mentor, blocked_mentor, peer, blocked_peer, mentor_role, postgres_competence])
    await db_session.flush()
    db_session.add_all(
        [
            UserRole(user_id=mentor.id, role_id=mentor_role.id),
            UserRole(user_id=blocked_mentor.id, role_id=mentor_role.id),
            UserCompetence(user_id=mentor.id, competence_id=postgres_competence.id),
            UserCompetence(user_id=blocked_mentor.id, competence_id=postgres_competence.id),
            UserTechnologyStack(user_id=peer.id, technology="PostgreSQL", level="know"),
            UserTechnologyStack(user_id=blocked_peer.id, technology="PostgreSQL", level="know"),
            AttendanceRecord(user_id=mentor.id, date=date(2026, 10, 13)),
            AttendanceRecord(user_id=blocked_mentor.id, date=date(2026, 10, 14)),
            AttendanceRecord(user_id=peer.id, date=date(2026, 10, 12)),
            AttendanceRecord(user_id=blocked_peer.id, date=date(2026, 10, 14)),
        ]
    )
    await db_session.flush()
    repository = UserTechnologyStackRepository()

    mentor_candidates = await repository.find_mentor_competences(db_session)
    peer_candidates = await repository.find_known_technology_helpers(
        db_session,
        user_id=requester.id,
        technology="PostgreSQL",
    )

    assert {candidate.helper.user_id for candidate in mentor_candidates} == {mentor.id}
    assert [candidate.competence_name for candidate in mentor_candidates] == ["постгрес"]
    assert [candidate.user_id for candidate in peer_candidates] == [peer.id]
