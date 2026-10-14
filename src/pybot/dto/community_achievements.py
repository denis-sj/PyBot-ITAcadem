"""DTOs for community achievement reads and publications."""

from collections.abc import Sequence
from datetime import datetime

from .base_dto import BaseDTO


class AchievementAwardReadDTO(BaseDTO):
    """An achievement shown in a participant's profile."""

    code: str
    achieved_at: datetime


class CommunityAchievementSummaryDTO(BaseDTO):
    """Current attendance streak and immutable achievement history."""

    current_streak: int
    achievements: Sequence[AchievementAwardReadDTO]


class AchievementPublicationDTO(BaseDTO):
    """A user's award row prepared for a public achievement digest."""

    id: int
    user_id: int
    first_name: str
    last_name: str | None
    telegram_username: str | None
    code: str
    achieved_at: datetime
