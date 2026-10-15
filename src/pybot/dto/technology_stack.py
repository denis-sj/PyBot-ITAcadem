"""DTOs for parsed, persisted and recommended participant technologies."""

from datetime import date, datetime
from typing import Annotated

from pydantic import Field

from ..domain.services.technology_stack import TechnologyLevel
from .base_dto import BaseDTO

TechnologyName = Annotated[str, Field(min_length=1, max_length=40, strict=True)]


class TechnologyStackItemDTO(BaseDTO):
    """One parsed or normalized technology and its self-reported level."""

    technology: TechnologyName
    level: TechnologyLevel


class ParsedTechnologyStackDTO(BaseDTO):
    """Structured output expected from the model when it parses free text."""

    items: tuple[TechnologyStackItemDTO, ...] = Field(max_length=20)


class TechnologyStackReadDTO(TechnologyStackItemDTO):
    """Persisted stack entry with its last update time."""

    updated_at: datetime


class TechnologyStackHelperDTO(BaseDTO):
    """A person who can help with a technology, safe for participant display."""

    user_id: int
    first_name: str
    last_name: str | None
    telegram_username: str | None
    is_mentor: bool
    last_visit: date | None

    @property
    def full_name(self) -> str:
        """Return the person's name without manufacturing a Telegram link."""
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()


class TechnologyStackRecommendationDTO(BaseDTO):
    """Helpers recommended for one technology the participant is learning."""

    technology: str
    helpers: tuple[TechnologyStackHelperDTO, ...]


class TechnologyStackMentorCompetenceDTO(BaseDTO):
    """A mentor and one competence name used for alias-aware matching."""

    helper: TechnologyStackHelperDTO
    competence_name: str
