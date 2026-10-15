"""Outbound delivery contract for community meeting announcements and invitations."""

from abc import ABC, abstractmethod


class MeetingDeliveryPort(ABC):
    """Send meeting messages with RSVP controls without exposing Telegram types to services."""

    @abstractmethod
    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        """Publish a group announcement with attendance buttons."""

    @abstractmethod
    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        """Send a private invitation with attendance buttons."""

    @abstractmethod
    async def send_text(self, telegram_id: int, text: str) -> None:
        """Send a plain reminder or meeting update."""
