"""Validated data contracts for meetings, audiences, invitations and RSVPs."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from .base_dto import BaseDTO

MeetingTitle = Annotated[str, Field(min_length=1, max_length=120, strict=True)]
MeetingVenue = Annotated[str, Field(min_length=1, max_length=200, strict=True)]
MeetingDescription = Annotated[str | None, Field(max_length=2000, strict=True)]
MeetingStartTime = Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", strict=True)]
MeetingAudienceKind = Literal["all", "competence", "stack"]
MeetingAnswer = Literal["going", "not_going"]


class CommunityMeetingDraftDTO(BaseDTO):
    """Agent supplied meeting draft; code validates permissions and the audience."""

    title: MeetingTitle
    meeting_date: date
    start_time: MeetingStartTime
    venue: MeetingVenue
    audience_kind: MeetingAudienceKind
    audience_target: Annotated[str | None, Field(max_length=100, strict=True)] = None
    description: MeetingDescription = None

    @field_validator("title", "venue", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Значение не может быть пустым.")
        return normalized

    @field_validator("audience_target", mode="before")
    @classmethod
    def normalize_audience_target(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None


class CommunityMeetingCreateDTO(BaseDTO):
    """Fully normalized persistence values for one meeting and its audience snapshot."""

    title: MeetingTitle
    starts_at: datetime
    venue: MeetingVenue
    description: MeetingDescription = None
    audience: Annotated[str, Field(min_length=3, max_length=200, strict=True)]
    creator_user_id: int
    created_at: datetime
    invitee_user_ids: tuple[int, ...] = ()


class CommunityMeetingReadDTO(BaseDTO):
    """A scheduled meeting safe to show to an authorized participant."""

    id: int
    title: str
    starts_at: datetime
    venue: str
    description: str | None
    audience: str
    creator_user_id: int
    status: str


class MeetingAudienceMemberDTO(BaseDTO):
    """A user in the resolved audience snapshot and their outbound eligibility."""

    user_id: int
    telegram_id: int
    bot_is_blocked: bool
    invitations_enabled: bool


class MeetingResponseDTO(BaseDTO):
    """The current RSVP answer and display name for one participant."""

    user_id: int
    display_name: str
    response: MeetingAnswer


class MeetingReminderRecipientDTO(BaseDTO):
    """An eligible Telegram recipient for one reminder pass."""

    user_id: int
    telegram_id: int
