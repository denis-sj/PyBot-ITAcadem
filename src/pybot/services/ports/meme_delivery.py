"""Outbound contract for publishing and deleting meme messages in Telegram."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ...dto.meme_of_the_week import MemeImageDTO


class MemeDeliveryError(Exception):
    """Safe error from Telegram meme delivery, with optional IDs for rollback."""

    def __init__(self, message: str, *, sent_message_ids: Sequence[int] = ()) -> None:
        super().__init__(message)
        self.sent_message_ids = tuple(sent_message_ids)


class MemeDeliveryPort(ABC):
    """Send media and plain text to the configured Telegram topic."""

    @abstractmethod
    async def send_images(
        self,
        *,
        chat_id: int,
        topic_id: int,
        images: Sequence[MemeImageDTO],
        caption: str,
    ) -> Sequence[int]:
        """Send one photo/animation or one ordered media album and return all message IDs."""

    @abstractmethod
    async def send_text(self, *, chat_id: int, topic_id: int, text: str) -> int:
        """Send a plain-text continuation in the same forum topic."""

    @abstractmethod
    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        """Delete a previously published meme message."""
