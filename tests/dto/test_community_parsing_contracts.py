from datetime import date

import pytest
from pydantic import ValidationError

from pybot.domain.services.community_checkins import CheckinStatus
from pybot.domain.services.technology_stack import TechnologyLevel
from pybot.dto.community_checkins import ParsedCheckinResponseDTO
from pybot.dto.community_meetings import CommunityMeetingDraftDTO
from pybot.dto.technology_stack import ParsedTechnologyStackDTO


@pytest.mark.parametrize(
    "changes",
    (
        {"status": "grant_admin"},
        {"summary": "x" * 201},
        {"summary": 123},
        {"help_topic": "x" * 41},
        {"help_topic": 123},
        {"new_goal": "x" * 201},
        {"unexpected": "ignored fields are forbidden"},
    ),
)
def test_checkin_extraction_contract_rejects_invalid_or_unbounded_output(changes: dict[str, object]) -> None:
    payload: dict[str, object] = {"summary": "Краткий итог", "status": CheckinStatus.IN_PROGRESS}
    payload.update(changes)

    with pytest.raises(ValidationError):
        ParsedCheckinResponseDTO.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"items": [{"technology": "x" * 41, "level": TechnologyLevel.KNOW}]},
        {"items": [{"technology": "Python", "level": "admin"}]},
        {"items": [{"technology": "Python", "level": TechnologyLevel.KNOW, "extra": True}]},
        {"items": [{"technology": "Python", "level": TechnologyLevel.KNOW}] * 21},
        {"items": [{"technology": 123, "level": TechnologyLevel.KNOW}]},
    ),
)
def test_stack_extraction_contract_rejects_invalid_or_unbounded_output(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ParsedTechnologyStackDTO.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("title", "x" * 121),
        ("venue", "x" * 201),
        ("description", "x" * 2001),
        ("audience_target", "x" * 101),
        ("start_time", "14:0"),
        ("untrusted_extra", "unexpected"),
    ),
)
def test_meeting_draft_contract_rejects_invalid_or_unbounded_fields(field_name: str, value: str) -> None:
    payload: dict[str, object] = {
        "title": "Разбор Python",
        "meeting_date": date(2026, 10, 18),
        "start_time": "14:00",
        "venue": "Коворкинг",
        "audience_kind": "all",
    }
    payload[field_name] = value

    with pytest.raises(ValidationError):
        CommunityMeetingDraftDTO.model_validate(payload)
