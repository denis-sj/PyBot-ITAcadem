"""Telegram adapter for the pulse report document port."""

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import BufferedInputFile

from ...services.ports import NotificationBlockedError, NotificationPermanentError, NotificationTemporaryError
from ...services.ports.pulse_report_delivery import PulseReportDeliveryPort


class TelegramPulseReportDelivery(PulseReportDeliveryPort):
    """Upload report bytes through Telegram's document API."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_pdf(self, *, recipient_id: int, filename: str, content: bytes, caption: str) -> None:
        """Send one buffered PDF and map expected Telegram failures to app errors."""
        try:
            await self._bot.send_document(
                chat_id=recipient_id,
                document=BufferedInputFile(content, filename=filename),
                caption=caption,
            )
        except TelegramForbiddenError as error:
            raise NotificationBlockedError("Telegram recipient blocked the bot") from error
        except TelegramRetryAfter as error:
            raise NotificationTemporaryError(
                "Telegram rate limit encountered", retry_after_seconds=float(error.retry_after)
            ) from error
        except (TelegramNetworkError, TelegramServerError) as error:
            raise NotificationTemporaryError("Temporary Telegram delivery failure") from error
        except (TelegramBadRequest, TelegramUnauthorizedError) as error:
            raise NotificationPermanentError("Permanent Telegram document failure") from error
        except TelegramAPIError as error:
            raise NotificationPermanentError("Telegram API document failure") from error
