"""Unit checks for group publication outcome reporting."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.config import AppSettings, YEKATERINBURG_TZ
from pybot.dto.community_meetings import CommunityMeetingDraftDTO, CommunityMeetingReadDTO
from pybot.infrastructure.community_meeting_repository import CommunityMeetingRepository
from pybot.services.community_meetings import CommunityMeetingService
from pybot.services.ports import MeetingDeliveryPort


@pytest.mark.asyncio
async def test_group_publication_failure_reports_zero_delivered_and_one_failed(
    settings_obj: AppSettings,
) -> None:
    settings_obj.events_chat_id = -1001234567890
    settings_obj.events_topic_id = 17
    database = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=CommunityMeetingRepository)
    repository.find_user_roles.return_value = frozenset({"Admin"})
    repository.count_users.return_value = 8
    repository.create_meeting.return_value = CommunityMeetingReadDTO(
        id=1,
        title="Разбор Docker Compose",
        starts_at=datetime(2026, 10, 18, 9, 0, tzinfo=UTC),
        venue="Коворкинг на Ленина, 5",
        description=None,
        audience="all",
        creator_user_id=7,
        status="scheduled",
    )
    delivery = AsyncMock(spec=MeetingDeliveryPort)
    delivery.send_announcement.side_effect = RuntimeError("Telegram unavailable")
    service = CommunityMeetingService(database, repository, settings_obj, delivery)
    draft = CommunityMeetingDraftDTO(
        title="Разбор Docker Compose",
        meeting_date=date(2026, 10, 18),
        start_time="14:00",
        venue="Коворкинг на Ленина, 5",
        audience_kind="all",
    )

    result = await service.create_meeting(
        7,
        draft,
        now=datetime(2026, 10, 15, 12, 0, tzinfo=YEKATERINBURG_TZ),
    )

    assert "Доставлено 0, не удалось 1" in result
    repository.create_meeting.assert_awaited_once()
    delivery.send_announcement.assert_awaited_once_with(
        -1001234567890,
        17,
        "Встреча «Разбор Docker Compose»\nв воскресенье, 18 октября, в 14:00\nМесто: Коворкинг на Ленина, 5",
        1,
    )
