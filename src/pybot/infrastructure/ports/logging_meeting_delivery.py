"""Logging adapter for meeting announcements and participant notifications."""

from ...core import logger
from ...services.ports import MeetingDeliveryPort


class LoggingMeetingDelivery(MeetingDeliveryPort):
    """Record outbound meeting messages without making Telegram API calls."""

    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        del chat_id, topic_id, text, meeting_id
        logger.info("event=community_meeting_announcement backend=logging")

    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        del telegram_id, text, meeting_id
        logger.info("event=community_meeting_invitation backend=logging")

    async def send_text(self, telegram_id: int, text: str) -> None:
        del telegram_id, text
        logger.info("event=community_meeting_message backend=logging")
