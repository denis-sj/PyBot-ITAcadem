"""Application use cases for participant stacks and helper recommendations."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.services.technology_stack import (
    MAX_TECHNOLOGY_STACK_SIZE,
    TechnologyLevel,
    TechnologyNotInStackError,
    TechnologyStackItem,
    TechnologyStackValidationError,
    normalize_technology_name,
    normalize_technology_stack,
    technology_identity,
)
from ..dto.community_checkins import PulseTechnologyDTO
from ..dto.technology_stack import (
    TechnologyStackHelperDTO,
    TechnologyStackItemDTO,
    TechnologyStackReadDTO,
    TechnologyStackRecommendationDTO,
)
from ..infrastructure.user_technology_stack_repository import UserTechnologyStackRepository

_HELPER_LIMIT = 3


class TechnologyStackService:
    """Validate stack changes, persist them atomically and find peer helpers."""

    def __init__(self, db: AsyncSession, repository: UserTechnologyStackRepository) -> None:
        self._db = db
        self._repository = repository

    async def get_user_stack(self, user_id: int) -> Sequence[TechnologyStackReadDTO]:
        """Read a participant's stack in canonical technology order."""
        return await self._repository.find_user_stack(self._db, user_id)

    async def get_stacks_for_users(
        self,
        user_ids: Sequence[int],
    ) -> dict[int, tuple[TechnologyStackReadDTO, ...]]:
        """Read several participant stacks with one repository query."""
        return await self._repository.find_stacks_for_users(self._db, user_ids)

    async def get_top_learning_technologies(self, limit: int = 5) -> Sequence[PulseTechnologyDTO]:
        """Return the most common technologies that participants are learning."""
        return await self._repository.find_top_learning_technologies(self._db, limit=limit)

    async def replace_user_stack(
        self,
        user_id: int,
        items: Sequence[TechnologyStackItemDTO],
    ) -> Sequence[TechnologyStackItemDTO]:
        """Replace all entries after canonicalizing and validating a parsed draft."""
        normalized_items = normalize_technology_stack(
            tuple(TechnologyStackItem(item.technology, item.level) for item in items)
        )
        await self._repository.replace_user_stack(self._db, user_id=user_id, items=normalized_items)
        await self._db.commit()
        return tuple(TechnologyStackItemDTO(technology=item.technology, level=item.level) for item in normalized_items)

    async def validate_set_user_technology(self, user_id: int, technology: str, level: TechnologyLevel) -> None:
        """Check a one-item action before the agent displays its confirmation."""
        item = self._normalize_item(technology, level)
        if await self._repository.has_user_stack_item(
            self._db,
            user_id=user_id,
            technology=item.technology,
        ):
            return
        if await self._repository.user_stack_size(self._db, user_id) >= MAX_TECHNOLOGY_STACK_SIZE:
            raise TechnologyStackValidationError("В стеке уже 20 технологий. Сначала уберите одну из них.")

    async def set_user_technology(self, user_id: int, technology: str, level: TechnologyLevel) -> None:
        """Add or update one technology without replacing the other entries."""
        item = self._normalize_item(technology, level)
        await self._repository.set_user_stack_item(self._db, user_id=user_id, item=item)
        await self._db.commit()

    async def validate_remove_user_technology(self, user_id: int, technology: str) -> None:
        """Check that one requested technology exists before confirmation."""
        canonical_name = normalize_technology_name(technology)
        if not await self._repository.has_user_stack_item(
            self._db,
            user_id=user_id,
            technology=canonical_name,
        ):
            raise TechnologyNotInStackError(canonical_name)

    async def remove_user_technology(self, user_id: int, technology: str) -> None:
        """Remove only the named technology from the participant's stack."""
        canonical_name = normalize_technology_name(technology)
        removed = await self._repository.remove_user_stack_item(
            self._db,
            user_id=user_id,
            technology=canonical_name,
        )
        if not removed:
            raise TechnologyNotInStackError(canonical_name)
        await self._db.commit()

    async def find_helpers(self, user_id: int, raw_technology: str) -> TechnologyStackRecommendationDTO:
        """Recommend matching mentors first, then peers who know the technology."""
        technology = normalize_technology_name(raw_technology)
        identity = technology_identity(technology)
        mentor_competences = await self._repository.find_mentor_competences(self._db)
        mentors_by_user: dict[int, TechnologyStackHelperDTO] = {}
        for mentor_competence in mentor_competences:
            if mentor_competence.helper.user_id == user_id:
                continue
            try:
                competence_identity = technology_identity(mentor_competence.competence_name)
            except TechnologyStackValidationError:
                continue
            if competence_identity == identity:
                mentors_by_user[mentor_competence.helper.user_id] = mentor_competence.helper

        mentor_helpers = self._sort_helpers(tuple(mentors_by_user.values()))
        selected_helpers = mentor_helpers[:_HELPER_LIMIT]
        if len(selected_helpers) < _HELPER_LIMIT:
            known_helpers = await self._repository.find_known_technology_helpers(
                self._db,
                user_id=user_id,
                technology=technology,
            )
            already_selected = {helper.user_id for helper in selected_helpers}
            selected_helpers.extend(
                helper
                for helper in self._sort_helpers(known_helpers)
                if helper.user_id != user_id and helper.user_id not in already_selected
            )
        return TechnologyStackRecommendationDTO(
            technology=technology,
            helpers=tuple(selected_helpers[:_HELPER_LIMIT]),
        )

    async def recommend_for_learning_items(
        self,
        user_id: int,
        items: Sequence[TechnologyStackItemDTO],
    ) -> tuple[TechnologyStackRecommendationDTO, ...]:
        """Build one recommendation for every technology the user is learning."""
        recommendations: list[TechnologyStackRecommendationDTO] = []
        for item in items:
            if item.level == TechnologyLevel.LEARNING:
                recommendations.append(await self.find_helpers(user_id, item.technology))
        return tuple(recommendations)

    @staticmethod
    def _normalize_item(technology: str, level: TechnologyLevel) -> TechnologyStackItem:
        return TechnologyStackItem(normalize_technology_name(technology), level)

    @staticmethod
    def _sort_helpers(helpers: Sequence[TechnologyStackHelperDTO]) -> list[TechnologyStackHelperDTO]:
        sorted_helpers = list(helpers)
        sorted_helpers.sort(key=lambda helper: helper.full_name.casefold())
        sorted_helpers.sort(key=lambda helper: helper.last_visit or _OLDEST_DATE, reverse=True)
        return sorted_helpers


_OLDEST_DATE = date.min
