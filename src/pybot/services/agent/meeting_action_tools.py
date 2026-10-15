"""Confirmed agent actions for creating and managing community meetings."""

from datetime import date

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart

from ...domain.exceptions import DomainError
from ...dto.community_meetings import (
    CommunityMeetingDraftDTO,
    MeetingAudienceKind,
    MeetingDescription,
    MeetingStartTime,
    MeetingTitle,
    MeetingVenue,
)
from .dependencies import AgentDependencies

MeetingId = int
_MEETING_ID_ARGUMENT = "meeting_id"


async def validate_meeting_creation(  # noqa: PLR0913
    ctx: RunContext[AgentDependencies],
    title: MeetingTitle,
    meeting_date: date,
    start_time: MeetingStartTime,
    venue: MeetingVenue,
    audience_kind: MeetingAudienceKind,
    audience_target: str | None = Field(default=None, max_length=100, strict=True),
    description: MeetingDescription = None,
) -> None:
    """Validate actor permissions and the exact meeting draft before presenting it."""
    user = await ctx.deps.get_actor()
    draft = CommunityMeetingDraftDTO(
        title=title,
        meeting_date=meeting_date,
        start_time=start_time,
        venue=venue,
        audience_kind=audience_kind,
        audience_target=audience_target,
        description=description,
    )
    try:
        await ctx.deps.actions.preview_meeting_creation(user.id, draft)
    except DomainError as error:
        raise ModelRetry(str(error)) from error


async def create_community_meeting(  # noqa: PLR0913
    ctx: RunContext[AgentDependencies],
    title: MeetingTitle,
    meeting_date: date,
    start_time: MeetingStartTime,
    venue: MeetingVenue,
    audience_kind: MeetingAudienceKind,
    audience_target: str | None = Field(default=None, max_length=100, strict=True),
    description: MeetingDescription = None,
) -> str:
    """Create and publish a community meeting after explicit author confirmation."""
    user = await ctx.deps.get_actor()
    draft = CommunityMeetingDraftDTO(
        title=title,
        meeting_date=meeting_date,
        start_time=start_time,
        venue=venue,
        audience_kind=audience_kind,
        audience_target=audience_target,
        description=description,
    )
    try:
        return await ctx.deps.actions.create_meeting(user.id, draft)
    except DomainError:
        return "Встреча не создана: после подтверждения данные или права изменились."


async def validate_meeting_reschedule(
    ctx: RunContext[AgentDependencies],
    meeting_id: MeetingId,
    meeting_date: date,
    start_time: MeetingStartTime,
) -> None:
    """Check meeting ownership and the new strict date/time before confirmation."""
    user = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.preview_meeting_reschedule(user.id, meeting_id, meeting_date, start_time)
    except DomainError as error:
        raise ModelRetry(str(error)) from error


async def reschedule_community_meeting(
    ctx: RunContext[AgentDependencies], meeting_id: MeetingId, meeting_date: date, start_time: MeetingStartTime
) -> str:
    """Reschedule a community meeting after confirmation."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.reschedule_meeting(user.id, meeting_id, meeting_date, start_time)
    except DomainError:
        return "Встреча не перенесена: после подтверждения данные или права изменились."


async def validate_meeting_cancellation(ctx: RunContext[AgentDependencies], meeting_id: MeetingId) -> None:
    """Check meeting ownership and status before confirming cancellation."""
    user = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.preview_meeting_cancellation(user.id, meeting_id)
    except DomainError as error:
        raise ModelRetry(str(error)) from error


async def cancel_community_meeting(ctx: RunContext[AgentDependencies], meeting_id: MeetingId) -> str:
    """Cancel a community meeting after confirmation."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.cancel_meeting(user.id, meeting_id)
    except DomainError:
        return "Встреча не отменена: после подтверждения данные или права изменились."


async def preview_meeting_creation(call: ToolCallPart, dependencies: AgentDependencies) -> str:
    """Rebuild the exact creation preview from validated tool-call arguments."""
    user = await dependencies.get_actor()
    draft = CommunityMeetingDraftDTO.model_validate(call.args_as_dict())
    return await dependencies.actions.preview_meeting_creation(user.id, draft)


async def preview_meeting_reschedule(call: ToolCallPart, dependencies: AgentDependencies) -> str:
    """Rebuild the exact reschedule preview from validated tool-call arguments."""
    user = await dependencies.get_actor()
    arguments = call.args_as_dict()
    return await dependencies.actions.preview_meeting_reschedule(
        user.id,
        _required_int(arguments, _MEETING_ID_ARGUMENT),
        date.fromisoformat(_required_text(arguments, "meeting_date")),
        _required_text(arguments, "start_time"),
    )


async def preview_meeting_cancellation(call: ToolCallPart, dependencies: AgentDependencies) -> str:
    """Rebuild the exact cancellation preview from validated tool-call arguments."""
    user = await dependencies.get_actor()
    return await dependencies.actions.preview_meeting_cancellation(
        user.id, _required_int(call.args_as_dict(), _MEETING_ID_ARGUMENT)
    )


def meeting_confirmation_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Return a safe fallback summary; registry replaces it with the application preview."""
    del recipient
    action = {
        "create_community_meeting": "Создать встречу",
        "reschedule_community_meeting": "Перенести встречу",
        "cancel_community_meeting": "Отменить встречу",
    }.get(call.tool_name, "Подтвердить действие со встречей")
    return f"{action}?"


def _required_int(arguments: dict[str, object], name: str) -> int:
    value = arguments.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    return value


def _required_text(arguments: dict[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    return value
