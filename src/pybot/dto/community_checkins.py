"""DTOs for goals, check-ins, notification preferences and the weekly pulse report."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import Field, model_validator

from ..domain.services.community_checkins import CheckinStatus, GoalStatus
from .base_dto import BaseDTO
from .technology_stack import TechnologyStackItemDTO

GoalText = Annotated[str, Field(min_length=1, max_length=200, strict=True)]


class ParsedCheckinResponseDTO(BaseDTO):
    """Structured check-in fields extracted from a participant's transient message."""

    summary: Annotated[str, Field(max_length=200)] | None
    status: CheckinStatus
    help_topic: Annotated[str, Field(max_length=40)] | None = None
    new_goal: GoalText | None = None

    @model_validator(mode="after")
    def validate_summary_for_status(self) -> ParsedCheckinResponseDTO:
        """Do not keep an invented summary for an off-topic or non-substantive answer."""
        if self.status == CheckinStatus.NO_SUBSTANTIVE_ANSWER and self.summary is not None:
            raise ValueError("A non-substantive check-in must not contain a summary")
        if self.status != CheckinStatus.NO_SUBSTANTIVE_ANSWER and not self.summary:
            raise ValueError("A substantive check-in must contain a short summary")
        return self


class CommunityGoalReadDTO(BaseDTO):
    """Current or historical goal visible to its owner and the pulse report."""

    id: int
    text: str
    status: GoalStatus
    created_at: datetime
    achieved_at: datetime | None


class UserNotificationPreferencesDTO(BaseDTO):
    """All community notification switches with enabled defaults."""

    checkins_enabled: bool = True
    monthly_summary_enabled: bool = True
    achievement_notifications_enabled: bool = True
    streak_warnings_enabled: bool = True
    meeting_invitations_enabled: bool = True


class CheckinPromptRecipientDTO(BaseDTO):
    """A registered participant selected for one check-in campaign."""

    user_id: int
    telegram_id: int
    first_name: str
    goal_id: int | None
    goal_text: str | None
    has_technology_stack: bool


class CheckinReminderRecipientDTO(BaseDTO):
    """A participant who received a prompt but has not answered yet."""

    checkin_id: int
    user_id: int
    telegram_id: int
    first_name: str
    goal_text: str | None


class PulseReportRecipientDTO(BaseDTO):
    """An available administrator who receives the private pulse report."""

    user_id: int
    telegram_id: int


class StreakWarningRecipientDTO(BaseDTO):
    """A subscribed, unblocked Student eligible for the weekly streak check."""

    user_id: int
    telegram_id: int


class WeeklyCheckinReportRowDTO(BaseDTO):
    """Persisted check-in row joined with the participant and goal snapshot."""

    id: int
    user_id: int
    first_name: str
    last_name: str | None
    telegram_username: str | None
    goal_text: str | None
    summary: str | None
    status: CheckinStatus | None
    help_topic: str | None
    prompted_at: datetime | None
    answered_at: datetime | None


class CheckinParticipantDTO(BaseDTO):
    """One responder or non-responder row for the PDF report."""

    user_id: int
    first_name: str
    last_name: str | None
    telegram_username: str | None
    goal_text: str | None
    summary: str | None
    status: CheckinStatus | None
    visit_count: int
    current_streak: int
    last_visit: date | None
    silent_weeks: int
    helper_names: tuple[str, ...] = ()
    achievements: tuple[str, ...] = ()
    technology_stack: tuple[TechnologyStackItemDTO, ...] = ()

    @property
    def display_name(self) -> str:
        """Return the participant's display name without exposing a Telegram link."""
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    @property
    def mention(self) -> str:
        """Return a Telegram username when available, otherwise the participant's name."""
        if self.telegram_username:
            return f"@{self.telegram_username}"
        return self.display_name or "Имя не указано"


class PulseTechnologyDTO(BaseDTO):
    """A learning technology and the number of participants studying it."""

    technology: str
    participant_count: int


class PulseAchievementDTO(BaseDTO):
    """A community achievement earned during the report week."""

    code: str
    title: str
    participant_names: tuple[str, ...]


class PulseReportDTO(BaseDTO):
    """Complete data model for one fixed-template community pulse report."""

    week_start: date
    week_end: date
    report_date: date
    invited_count: int
    answered_count: int
    needs_help_count: int
    goals_achieved_count: int
    visit_count: int
    answered_delta: int
    needs_help_delta: int
    goals_achieved_delta: int
    visits_delta: int
    needs_help: tuple[CheckinParticipantDTO, ...]
    answered_participants: tuple[CheckinParticipantDTO, ...]
    achievements: tuple[PulseAchievementDTO, ...]
    non_responders: tuple[CheckinParticipantDTO, ...]
    learning_technologies: tuple[PulseTechnologyDTO, ...]

    @property
    def answer_ratio(self) -> str:
        """Return the report count pair in the template's compact format."""
        return f"{self.answered_count}/{self.invited_count}"
