"""Private check-in response routing, independent of agent chat handling."""

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Message
from aiogram_dialog.api.entities import Context
from dishka import FromDishka

from ....services.community_checkins import CommunityCheckinService

community_checkin_router = Router(name="community_checkin_response")


@community_checkin_router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def handle_private_checkin_response(
    message: Message,
    service: FromDishka[CommunityCheckinService],
    user_id: int | None = None,
    raw_state: str | None = None,
    aiogd_context: Context | None = None,
) -> None:
    """Use the next ordinary private message for an open check-in only."""
    if user_id is None or message.text is None or raw_state is not None or aiogd_context is not None:
        return UNHANDLED
    reply = await service.handle_response(user_id, message.text)
    if reply is None:
        return UNHANDLED
    await message.answer(reply)
