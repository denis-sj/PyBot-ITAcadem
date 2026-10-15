"""Модуль бота IT Academ."""

from typing import TypedDict

from aiogram_dialog import DialogManager

from ....texts.technology_stack import STACK_PARSE_RETRY


class RegistrationCompetenceOption(TypedDict):
    id: int
    name: str


async def get_registration_competencies(
    dialog_manager: DialogManager,
    **kwargs: object,
) -> dict[str, list[RegistrationCompetenceOption]]:
    """Вспомогательная функция get_registration_competencies."""
    del kwargs
    raw_options = dialog_manager.dialog_data.get("registration_competencies")
    return {"registration_competencies": _normalize_competence_options(raw_options)}


async def get_stack_confirmation(
    dialog_manager: DialogManager,
    **kwargs: object,
) -> dict[str, str]:
    """Return the parsed draft preview without reading or writing the database."""
    del kwargs
    raw_preview = dialog_manager.dialog_data.get("technology_stack_confirmation")
    preview = raw_preview if isinstance(raw_preview, str) else STACK_PARSE_RETRY
    return {"technology_stack_confirmation": preview}


def _normalize_competence_options(raw_options: object) -> list[RegistrationCompetenceOption]:
    if not isinstance(raw_options, list):
        return []

    normalized_options: list[RegistrationCompetenceOption] = []
    for option in raw_options:
        match option:
            case {"id": int(competence_id), "name": str(competence_name)}:
                normalized_options.append({"id": competence_id, "name": competence_name})

    return normalized_options
