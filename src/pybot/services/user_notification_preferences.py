"""Application service for participant notification preferences."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.services.community_checkins import NotificationPreferenceKey
from ..dto.community_checkins import UserNotificationPreferencesDTO
from ..infrastructure.community_achievement_repository import CommunityAchievementRepository
from ..infrastructure.user_notification_preference_repository import UserNotificationPreferenceRepository


class UserNotificationPreferencesService:
    """Read and update the five opt-out switches while retaining all-enabled defaults."""

    def __init__(
        self,
        db: AsyncSession,
        preference_repository: UserNotificationPreferenceRepository,
        achievement_repository: CommunityAchievementRepository,
    ) -> None:
        self._db = db
        self._preference_repository = preference_repository
        self._achievement_repository = achievement_repository

    async def get_preferences(self, user_id: int) -> UserNotificationPreferencesDTO:
        """Return the participant's preferences or all-enabled defaults."""
        return await self._preference_repository.find_preferences(self._db, user_id)

    async def toggle(self, user_id: int, setting: NotificationPreferenceKey) -> UserNotificationPreferencesDTO:
        """Toggle one opt-out and discard undelivered personal badges when opting out."""
        is_enabled = await self._preference_repository.toggle(self._db, user_id=user_id, setting=setting)
        if setting == NotificationPreferenceKey.ACHIEVEMENTS and not is_enabled:
            await self._achievement_repository.mark_all_user_awards_notified(self._db, user_id)
        await self._db.commit()
        return await self.get_preferences(user_id)

    async def set_checkins_enabled(self, user_id: int, is_enabled: bool) -> bool:
        """Enable or pause check-ins explicitly for the two public commands."""
        current = await self._preference_repository.find_preferences(self._db, user_id)
        if current.checkins_enabled == is_enabled:
            return False
        await self._preference_repository.set_checkins_enabled(self._db, user_id=user_id, is_enabled=is_enabled)
        await self._db.commit()
        return True
