"""Telegram adapter for meeting announcements, RSVP buttons and private notices."""

from aiogram import Bot
from aiogram.exceptions import (
    RestartingTelegram,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ...core import logger
from ...services.ports import (
    MeetingDeliveryPort,
    NotificationBlockedError,
    NotificationPermanentError,
    NotificationTemporaryError,
)


class TelegramMeetingDelivery(MeetingDeliveryPort):
    """Send meeting content using the configured aiogram Bot instance."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        await self._send(
            chat_id,
            text,
            reply_markup=self._keyboard(meeting_id),
            message_thread_id=topic_id,
        )

    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        await self._send(telegram_id, text, reply_markup=self._keyboard(meeting_id))

    async def send_text(self, telegram_id: int, text: str) -> None:
        await self._send(telegram_id, text)

    async def _send(
        self,
        recipient_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        try:
            await self._bot.send_message(
                chat_id=recipient_id,
                text=text,
                reply_markup=reply_markup,
                message_thread_id=message_thread_id,
                parse_mode=None,
            )
        except TelegramRetryAfter as error:
            raise NotificationTemporaryError(
                "Telegram rate limit encountered", retry_after_seconds=float(error.retry_after)
            ) from error
        except TelegramForbiddenError as error:
            raise NotificationBlockedError("Telegram recipient blocked the bot") from error
        except (TelegramNetworkError, TelegramServerError, RestartingTelegram) as error:
            raise NotificationTemporaryError("Temporary Telegram delivery failure") from error
        except (TelegramBadRequest, TelegramUnauthorizedError, TelegramNotFound, TelegramEntityTooLarge) as error:
            raise NotificationPermanentError("Permanent Telegram delivery failure") from error
        except TelegramAPIError as error:
            raise NotificationPermanentError("Telegram API delivery failure") from error
        except Exception as error:
            logger.warning(
                "event=community_meeting_telegram_send status=failed error_type={error_type}",
                error_type=type(error).__name__,
            )
            raise NotificationPermanentError("Unexpected meeting notification failure") from error

    @staticmethod
    def _keyboard(meeting_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Приду", callback_data=f"meet:{meeting_id}:going"),
                    InlineKeyboardButton(text="Не смогу", callback_data=f"meet:{meeting_id}:not_going"),
                ]
            ]
        )
