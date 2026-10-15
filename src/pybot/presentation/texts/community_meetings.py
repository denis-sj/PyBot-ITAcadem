"""Russian presentation texts and date formatting for community meetings."""

from datetime import datetime

from ...core.config import YEKATERINBURG_TZ
from ...dto.community_meetings import CommunityMeetingDraftDTO, CommunityMeetingReadDTO, MeetingResponseDTO

_WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_MEETING_TIME_FORMAT = "%H:%M"


def render_meeting_date(value: datetime) -> str:
    """Render an aware timestamp as a natural Yekaterinburg date and strict time."""
    local_value = value.astimezone(YEKATERINBURG_TZ)
    return (
        f"в {_WEEKDAYS[local_value.weekday()]}, {local_value.day} "
        f"{_MONTHS[local_value.month - 1]}, в {local_value:{_MEETING_TIME_FORMAT}}"
    )


def render_meeting_draft(
    draft: CommunityMeetingDraftDTO,
    starts_at: datetime,
    audience: str,
    recipients: int,
    publication_topic_name: str | None = None,
) -> str:
    """Show the exact action values and audience size before a user confirms."""
    description = f"\nОписание: {draft.description}" if draft.description else ""
    delivery_summary = (
        f"Анонс в группе: {publication_topic_name}"
        if publication_topic_name is not None
        else f"Приглашение получат: {recipients} чел."
    )
    return (
        f"Создать встречу «{draft.title}»?\n"
        f"Когда: {render_meeting_date(starts_at)}\n"
        f"Место: {draft.venue}\n"
        f"Аудитория: {audience}\n{delivery_summary}{description}"
    )


def render_meeting_announcement(title: str, starts_at: datetime, venue: str, description: str | None) -> str:
    """Format the group announcement without generated claims."""
    details = f"\n{description}" if description else ""
    return f"Встреча «{title}»\n{render_meeting_date(starts_at)}\nМесто: {venue}{details}"


def render_meeting_reminder(title: str, starts_at: datetime, lead: str) -> str:
    """Format one scheduled reminder for a going participant."""
    return f"Напоминание: встреча «{title}» начнётся {render_meeting_date(starts_at)} (через {lead})."


def render_meeting_rescheduled(title: str, starts_at: datetime) -> str:
    """Format a rescheduling notice for previously confirmed attendees."""
    return f"Встреча «{title}» перенесена. Новое время: {render_meeting_date(starts_at)}."


def render_meeting_cancelled(title: str) -> str:
    """Format a cancellation notice."""
    return f"Встреча «{title}» отменена."


def render_upcoming_meetings(meetings: tuple[CommunityMeetingReadDTO, ...]) -> str:
    """List meetings already filtered by the caller's audience access."""
    if not meetings:
        return "Ближайших встреч для тебя пока нет."
    rows = [
        f"• «{meeting.title}» — {render_meeting_date(meeting.starts_at)}; место: {meeting.venue} (ID {meeting.id})"
        for meeting in meetings
    ]
    return "Ближайшие встречи:\n" + "\n".join(rows)


def render_meeting_attendees(attendees: tuple[MeetingResponseDTO, ...]) -> str:
    """Format the attendees who answered «Приду»."""
    if not attendees:
        return "Пока никто не отметил, что придёт."
    return "Придут:\n" + "\n".join(f"• {attendee.display_name}" for attendee in attendees)
