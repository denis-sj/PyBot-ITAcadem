from datetime import date
from collections.abc import Sequence
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.domain.services.technology_stack import (
    TechnologyLevel,
    TechnologyStackItem,
    TechnologyStackValidationError,
)
from pybot.dto.technology_stack import (
    ParsedTechnologyStackDTO,
    TechnologyStackHelperDTO,
    TechnologyStackItemDTO,
    TechnologyStackMentorCompetenceDTO,
    TechnologyStackRecommendationDTO,
    TechnologyStackReadDTO,
)
from pybot.infrastructure.user_technology_stack_repository import UserTechnologyStackRepository
from pybot.presentation.texts.technology_stack import (
    render_helper_recommendation,
    render_stack_confirmation,
    render_stack_read,
)
from pybot.services.technology_stack import TechnologyStackService


class MemoryTechnologyStackRepository(UserTechnologyStackRepository):
    def __init__(self) -> None:
        self.mentor_competences: list[TechnologyStackMentorCompetenceDTO] = []
        self.known_helpers: list[TechnologyStackHelperDTO] = []
        self.stack: list[TechnologyStackReadDTO] = []
        self.stack_size = 0
        self.has_item = False
        self.replaced_items: Sequence[TechnologyStackItem] = ()
        self.set_items: list[TechnologyStackItem] = []
        self.removed_technology: str | None = None

    async def find_user_stack(self, db: AsyncSession, user_id: int) -> Sequence[TechnologyStackReadDTO]:
        _ = db, user_id
        return self.stack

    async def replace_user_stack(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        items: Sequence[TechnologyStackItem],
    ) -> None:
        _ = db, user_id
        self.replaced_items = items

    async def set_user_stack_item(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        item: TechnologyStackItem,
    ) -> bool:
        _ = db, user_id
        self.set_items.append(item)
        return True

    async def remove_user_stack_item(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        technology: str,
    ) -> bool:
        _ = db, user_id
        self.removed_technology = technology
        return True

    async def has_user_stack_item(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        technology: str,
    ) -> bool:
        _ = db, user_id, technology
        return self.has_item

    async def user_stack_size(self, db: AsyncSession, user_id: int) -> int:
        _ = db, user_id
        return self.stack_size

    async def find_mentor_competences(
        self,
        db: AsyncSession,
    ) -> Sequence[TechnologyStackMentorCompetenceDTO]:
        _ = db
        return self.mentor_competences

    async def find_known_technology_helpers(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        technology: str,
    ) -> Sequence[TechnologyStackHelperDTO]:
        _ = db, user_id, technology
        return self.known_helpers


def _helper(  # noqa: PLR0913
    user_id: int,
    *,
    first_name: str = "Участник",
    last_name: str | None = None,
    username: str | None = None,
    mentor: bool = False,
    last_visit: date | None = None,
) -> TechnologyStackHelperDTO:
    return TechnologyStackHelperDTO(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        telegram_username=username,
        is_mentor=mentor,
        last_visit=last_visit,
    )


def _service(repository: MemoryTechnologyStackRepository) -> tuple[TechnologyStackService, AsyncMock]:
    db = Mock()
    db.commit = AsyncMock()
    return TechnologyStackService(db, repository), db.commit


@pytest.mark.asyncio
async def test_replace_stack_canonicalizes_and_commits_as_one_operation() -> None:
    repository = MemoryTechnologyStackRepository()
    service, commit = _service(repository)

    saved = await service.replace_user_stack(
        17,
        (
            TechnologyStackItemDTO(technology="питон", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="DOCKER", level=TechnologyLevel.LEARNING),
        ),
    )

    assert [(item.technology, item.level) for item in saved] == [
        ("Python", TechnologyLevel.KNOW),
        ("Docker", TechnologyLevel.LEARNING),
    ]
    assert [item.technology for item in repository.replaced_items] == ["Python", "Docker"]
    commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_helper_search_matches_competence_alias_and_prioritizes_recent_mentors() -> None:
    repository = MemoryTechnologyStackRepository()
    mentor_recent = _helper(20, username="mentor_new", mentor=True, last_visit=date(2026, 10, 13))
    mentor_older = _helper(21, first_name="Анна", mentor=True, last_visit=date(2026, 10, 2))
    repository.mentor_competences = [
        TechnologyStackMentorCompetenceDTO(helper=_helper(17, mentor=True), competence_name="Docker"),
        TechnologyStackMentorCompetenceDTO(helper=mentor_recent, competence_name="ДОКЕР"),
        TechnologyStackMentorCompetenceDTO(helper=mentor_recent, competence_name="Docker"),
        TechnologyStackMentorCompetenceDTO(helper=mentor_older, competence_name="Docker"),
    ]
    repository.known_helpers = [
        _helper(22, first_name="Пётр", username="petr_peer", last_visit=date(2026, 10, 14)),
        _helper(23, first_name="Борис", last_visit=date(2026, 10, 1)),
    ]
    service, _ = _service(repository)

    result = await service.find_helpers(17, "докер")

    assert result.technology == "Docker"
    assert [item.user_id for item in result.helpers] == [20, 21, 22]
    assert [item.is_mentor for item in result.helpers] == [True, True, False]


@pytest.mark.asyncio
async def test_helper_search_excludes_caller_and_caps_peer_recommendations_at_three() -> None:
    repository = MemoryTechnologyStackRepository()
    repository.known_helpers = [
        _helper(
            user_id,
            first_name=f"Участник {user_id}",
            last_visit=date(2026, 10, 14 if user_id == 17 else user_id - 30),
        )
        for user_id in (17, 31, 32, 33, 34)
    ]
    service, _ = _service(repository)

    result = await service.find_helpers(17, "POSTGRES")

    assert result.technology == "PostgreSQL"
    assert [item.user_id for item in result.helpers] == [34, 33, 32]


@pytest.mark.asyncio
async def test_stack_action_preflight_rejects_the_twenty_first_item() -> None:
    repository = MemoryTechnologyStackRepository()
    repository.stack_size = 20
    service, commit = _service(repository)

    with pytest.raises(TechnologyStackValidationError):
        await service.validate_set_user_technology(17, "Kubernetes", TechnologyLevel.LEARNING)

    assert repository.set_items == []
    commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_parsed_stack_dto_rejects_more_than_twenty_technologies() -> None:
    with pytest.raises(ValueError):
        ParsedTechnologyStackDTO(
            items=tuple(
                TechnologyStackItemDTO(technology=f"Tool {index}", level=TechnologyLevel.KNOW) for index in range(21)
            )
        )


def test_stack_texts_show_preview_and_plain_names_without_links() -> None:
    preview = render_stack_confirmation(
        (
            TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="Docker", level=TechnologyLevel.LEARNING),
        )
    )
    recommendation = render_helper_recommendation(
        TechnologyStackRecommendationDTO(
            technology="Docker",
            helpers=(
                _helper(31, first_name="Пётр", last_name="Иванов", username=None),
                _helper(32, username="mentor", mentor=True),
            ),
        )
    )

    assert preview == "Записал: знаю — Python; изучаю — Docker."
    assert recommendation == "Кстати, в Docker у нас разбираются: Пётр Иванов, @mentor (ментор). Можешь написать им."
    assert "Стек пока пуст" in render_stack_read(())


def test_empty_helper_recommendation_says_to_ask_in_group() -> None:
    message = render_helper_recommendation(TechnologyStackRecommendationDTO(technology="Kubernetes", helpers=()))

    assert message == "Пока не нашёл, у кого можно спросить про Kubernetes. Попробуй спросить в группе."
