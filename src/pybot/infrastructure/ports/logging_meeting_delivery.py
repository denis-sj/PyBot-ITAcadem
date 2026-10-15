"""Logging adapter for meeting announcements and participant notifications."""

from ...core import logger
from ...services.ports import MeetingDeliveryPort


class LoggingMeetingDelivery(MeetingDeliveryPort):
    """Record outbound meeting messages without making Telegram API calls."""

    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        logger.info(
            "event=community_meeting_announcement backend=logging meeting_id={meeting_id} "
            "chat_id={chat_id} topic_id={topic_id} text={text}",
            meeting_id=meeting_id,
            chat_id=chat_id,
            topic_id=topic_id,
            text=text,
        )

    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        logger.info(
            "event=community_meeting_invitation backend=logging meeting_id={meeting_id} "
            "recipient_id={recipient_id} text={text}",
            meeting_id=meeting_id,
            recipient_id=telegram_id,
            text=text,
        )

    async def send_text(self, telegram_id: int, text: str) -> None:
        logger.info(
            "event=community_meeting_message backend=logging recipient_id={recipient_id} text={text}",
            recipient_id=telegram_id,
            text=text,
        )
