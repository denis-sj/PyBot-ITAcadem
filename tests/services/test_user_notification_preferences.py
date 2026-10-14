from unittest.mock import AsyncMock

import pytest

from pybot.domain.services.community_checkins import NotificationPreferenceKey
from pybot.dto.community_checkins import UserNotificationPreferencesDTO
from pybot.services.user_notification_preferences import UserNotificationPreferencesService


@pytest.mark.asyncio
async def test_disabling_personal_achievement_messages_consumes_pending_awards() -> None:
    db = AsyncMock()
    preferences = AsyncMock()
    achievements = AsyncMock()
    preferences.toggle.return_value = False
    preferences.find_preferences.return_value = UserNotificationPreferencesDTO(achievement_notifications_enabled=False)
    service = UserNotificationPreferencesService(db, preferences, achievements)

    result = await service.toggle(42, NotificationPreferenceKey.ACHIEVEMENTS)

    assert result.achievement_notifications_enabled is False
    preferences.toggle.assert_awaited_once_with(
        db,
        user_id=42,
        setting=NotificationPreferenceKey.ACHIEVEMENTS,
    )
    achievements.mark_all_user_awards_notified.assert_awaited_once_with(db, 42)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkin_pause_changes_only_the_checkin_switch() -> None:
    db = AsyncMock()
    preferences = AsyncMock()
    achievements = AsyncMock()
    preferences.find_preferences.return_value = UserNotificationPreferencesDTO()
    service = UserNotificationPreferencesService(db, preferences, achievements)

    changed = await service.set_checkins_enabled(42, False)

    assert changed is True
    preferences.set_checkins_enabled.assert_awaited_once_with(db, user_id=42, is_enabled=False)
    achievements.mark_all_user_awards_notified.assert_not_awaited()
    db.commit.assert_awaited_once()
