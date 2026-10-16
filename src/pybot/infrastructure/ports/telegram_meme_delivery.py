"""Telegram adapter for sending and deleting weekly meme messages."""

from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaDocument, InputMediaPhoto, MediaUnion

from ...dto.meme_of_the_week import MemeImageDTO
from ...services.ports.meme_delivery import MemeDeliveryError, MemeDeliveryPort


class TelegramMemeDelivery(MemeDeliveryPort):
    """Upload verified image bytes to a topic without parsing untrusted captions."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_images(
        self,
        *,
        chat_id: int,
        topic_id: int,
        images: Sequence[MemeImageDTO],
        caption: str,
    ) -> tuple[int, ...]:
        if not images:
            raise MemeDeliveryError("Meme publication has no images")
        files = tuple(
            BufferedInputFile(image.content, filename=f"meme_{index}{image.extension}")
            for index, image in enumerate(images, start=1)
        )
        try:
            if len(images) == 1 and images[0].is_animation:
                message = await self._bot.send_animation(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    animation=files[0],
                    caption=caption,
                    parse_mode=None,
                )
                return (message.message_id,)
            if len(images) == 1:
                message = await self._bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    photo=files[0],
                    caption=caption,
                    parse_mode=None,
                )
                return (message.message_id,)

            media: list[MediaUnion] = []
            if any(image.is_animation for image in images):
                media.extend(
                    InputMediaDocument(
                        media=file,
                        caption=caption if index == 0 else None,
                        parse_mode=None,
                    )
                    for index, file in enumerate(files)
                )
            else:
                media.extend(
                    InputMediaPhoto(
                        media=file,
                        caption=caption if index == 0 else None,
                        parse_mode=None,
                    )
                    for index, file in enumerate(files)
                )
            messages = await self._bot.send_media_group(
                chat_id=chat_id,
                message_thread_id=topic_id,
                media=media,
            )
            return tuple(message.message_id for message in messages)
        except TelegramAPIError as error:
            raise MemeDeliveryError("Telegram media delivery failed") from error
        except Exception as error:
            raise MemeDeliveryError("Unexpected Telegram media delivery failure") from error

    async def send_text(self, *, chat_id: int, topic_id: int, text: str) -> int:
        try:
            message = await self._bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=text,
                parse_mode=None,
            )
        except TelegramAPIError as error:
            raise MemeDeliveryError("Telegram text delivery failed") from error
        except Exception as error:
            raise MemeDeliveryError("Unexpected Telegram text delivery failure") from error
        return message.message_id

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as error:
            if "message to delete not found" in error.message.casefold():
                return
            raise MemeDeliveryError("Telegram message deletion failed") from error
        except TelegramAPIError as error:
            raise MemeDeliveryError("Telegram message deletion failed") from error
        except Exception as error:
            raise MemeDeliveryError("Unexpected Telegram message deletion failure") from error
