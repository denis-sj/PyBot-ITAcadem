"""Telegram callback handling for RSVP buttons on community meetings."""

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka import FromDishka

from ....domain.exceptions import DomainError
from ....services.community_meetings import CommunityMeetingService

community_meetings_router = Router(name="community_meetings")
_MEETING_CALLBACK_PARTS = 3


@community_meetings_router.callback_query(F.data.startswith("meet:"))
async def handle_meeting_response(
    callback: CallbackQuery,
    service: FromDishka[CommunityMeetingService],
    user_id: int | None = None,
) -> None:
    """Validate meeting, response and audience before recording an RSVP."""
    meeting_id, response = _parse_callback_data(callback.data)
    if meeting_id is None or response is None:
        await callback.answer("Кнопка встречи недействительна.", show_alert=True)
        return
    if user_id is None:
        await callback.answer("Сначала зарегистрируйтесь в боте.", show_alert=True)
        return
    try:
        result = await service.respond(meeting_id, user_id, response)
    except DomainError as error:
        await callback.answer(error.message, show_alert=True)
        return
    await callback.answer(result)


def _parse_callback_data(data: str | None) -> tuple[int | None, str | None]:
    if data is None:
        return None, None
    parts = data.split(":")
    if len(parts) != _MEETING_CALLBACK_PARTS or parts[0] != "meet" or parts[2] not in {"going", "not_going"}:
        return None, None
    try:
        meeting_id = int(parts[1])
    except ValueError:
        return None, None
    return (meeting_id, parts[2]) if meeting_id > 0 else (None, None)
