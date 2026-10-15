"""Pure rules for normalizing participant technologies and learning levels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..exceptions import DomainError

MAX_TECHNOLOGY_STACK_SIZE = 20
MAX_TECHNOLOGY_NAME_LENGTH = 40


class TechnologyLevel(StrEnum):
    """A participant's self-reported familiarity with a technology."""

    KNOW = "know"
    LEARNING = "learning"


@dataclass(frozen=True, slots=True)
class TechnologyStackItem:
    """One canonical technology and its self-reported learning level."""

    technology: str
    level: TechnologyLevel


class TechnologyStackValidationError(DomainError):
    """A submitted technology stack is empty or exceeds its data limits."""

    def __init__(self, message: str = "Не удалось распознать стек технологий.") -> None:
        super().__init__(message, code="INVALID_TECHNOLOGY_STACK")


class TechnologyNotInStackError(DomainError):
    """The requested technology is not present in the participant's stack."""

    def __init__(self, technology: str) -> None:
        super().__init__(f"Технологии «{technology}» нет в вашем стеке.", code="TECHNOLOGY_NOT_IN_STACK")


_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "Docker": ("docker", "докер", "докер дэсктоп"),
    "Docker Compose": ("docker compose", "docker-compose", "докер компоуз", "компоуз"),
    "Python": ("python", "python 3", "питон", "питон 3", "питон3"),
    "JavaScript": ("javascript", "java script", "js", "джаваскрипт", "джава скрипт"),
    "TypeScript": ("typescript", "type script", "ts", "тайпскрипт"),
    "PostgreSQL": (
        "postgresql",
        "postgres",
        "postgre sql",
        "постгрес",
        "постгресql",
        "постгрэскьюэль",
    ),
    "SQL": ("sql", "скьюэль", "эс кью эл"),
    "Git": ("git", "гит"),
    "Linux": ("linux", "линукс"),
    "HTML": ("html", "хтмл"),
    "CSS": ("css", "цсс"),
    "React": ("react", "реакт"),
    "React Native": ("react native", "реакт нейтив"),
    "Node.js": ("node.js", "nodejs", "node js", "нод", "нода", "нод js"),
    "FastAPI": ("fastapi", "fast api", "фастапи"),
    "Django": ("django", "джанго"),
    "Redis": ("redis", "редис"),
    "Java": ("java", "джава"),
    "Kubernetes": ("kubernetes", "k8s", "кубернетес", "кубер"),
    "C++": ("c++", "си++"),
    "C#": ("c#", "си#", "си шарп"),
}


def _normalize_alias_key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


_ALIASES = {
    _normalize_alias_key(alias): canonical
    for canonical, aliases in _CANONICAL_ALIASES.items()
    for alias in (canonical, *aliases)
}


def normalize_technology_name(raw_name: str) -> str:
    """Return a canonical catalog name or a safely trimmed user-provided name."""
    trimmed_name = raw_name.strip()
    if not trimmed_name or not any(character.isalnum() for character in trimmed_name):
        raise TechnologyStackValidationError("Укажите название технологии буквами или цифрами.")
    if len(trimmed_name) > MAX_TECHNOLOGY_NAME_LENGTH:
        raise TechnologyStackValidationError(
            f"Название технологии не должно быть длиннее {MAX_TECHNOLOGY_NAME_LENGTH} символов."
        )

    alias_key = _normalize_alias_key(trimmed_name)
    canonical_name = _ALIASES.get(alias_key)
    if canonical_name is not None:
        return canonical_name
    return trimmed_name[0].upper() + trimmed_name[1:]


def technology_identity(raw_name: str) -> str:
    """Return a case-insensitive comparison key that also resolves known aliases."""
    return normalize_technology_name(raw_name).casefold()


def normalize_technology_stack(items: Sequence[TechnologyStackItem]) -> tuple[TechnologyStackItem, ...]:
    """Canonicalize a parsed stack, keeping the last level for repeated technologies."""
    normalized_items: dict[str, TechnologyStackItem] = {}
    for item in items:
        technology = normalize_technology_name(item.technology)
        normalized_items[technology.casefold()] = TechnologyStackItem(technology=technology, level=item.level)

    if not normalized_items:
        raise TechnologyStackValidationError("Не получилось распознать технологии. Попробуйте написать их иначе.")
    if len(normalized_items) > MAX_TECHNOLOGY_STACK_SIZE:
        raise TechnologyStackValidationError(f"В стеке может быть не больше {MAX_TECHNOLOGY_STACK_SIZE} технологий.")
    return tuple(normalized_items.values())
