from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.types import InputMediaDocument, InputMediaPhoto

from pybot.dto.meme_of_the_week import MemeImageDTO
from pybot.infrastructure.ports.telegram_meme_delivery import TelegramMemeDelivery
from pybot.services.ports.meme_delivery import MemeDeliveryError
from tests.agent.conftest import build_message


def _image(url: str, content_type: str, extension: str) -> MemeImageDTO:
    return MemeImageDTO(url, b"verified-image-bytes", content_type, extension)


@pytest.mark.asyncio
async def test_single_gif_is_sent_as_animation_with_plain_caption() -> None:
    bot = Mock(spec=Bot)
    bot.send_animation = AsyncMock(return_value=build_message(sender_is_bot=True, message_id=21))
    delivery = TelegramMemeDelivery(bot)

    ids = await delivery.send_images(
        chat_id=-100123,
        topic_id=42,
        images=[_image("https://cdn.test/one.gif", "image/gif", ".gif")],
        caption="😂 Мем недели\nGIF title",
    )

    assert ids == (21,)
    animation_call = bot.send_animation.await_args
    assert animation_call is not None
    kwargs = animation_call.kwargs
    assert kwargs["chat_id"] == -100123
    assert kwargs["message_thread_id"] == 42
    assert kwargs["parse_mode"] is None
    assert kwargs["caption"] == "😂 Мем недели\nGIF title"
    bot.send_photo.assert_not_awaited()
    bot.send_media_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_gif_album_uses_ordered_documents_to_keep_original_files() -> None:
    bot = Mock(spec=Bot)
    bot.send_media_group = AsyncMock(
        return_value=[
            build_message(sender_is_bot=True, message_id=31),
            build_message(sender_is_bot=True, message_id=32),
        ]
    )
    delivery = TelegramMemeDelivery(bot)

    ids = await delivery.send_images(
        chat_id=-100123,
        topic_id=42,
        images=[
            _image("https://cdn.test/one.jpg", "image/jpeg", ".jpg"),
            _image("https://cdn.test/two.gif", "image/gif", ".gif"),
        ],
        caption="Caption",
    )

    assert ids == (31, 32)
    media_call = bot.send_media_group.await_args
    assert media_call is not None
    media = media_call.kwargs["media"]
    assert all(isinstance(item, InputMediaDocument) for item in media)
    assert [item.caption for item in media] == ["Caption", None]
    assert media_call.kwargs["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_photo_album_uses_photo_media() -> None:
    bot = Mock(spec=Bot)
    bot.send_media_group = AsyncMock(
        return_value=[
            build_message(sender_is_bot=True, message_id=41),
            build_message(sender_is_bot=True, message_id=42),
        ]
    )
    delivery = TelegramMemeDelivery(bot)

    ids = await delivery.send_images(
        chat_id=-100123,
        topic_id=42,
        images=[
            _image("https://cdn.test/one.png", "image/png", ".png"),
            _image("https://cdn.test/two.webp", "image/webp", ".webp"),
        ],
        caption="Caption",
    )

    assert ids == (41, 42)
    media_call = bot.send_media_group.await_args
    assert media_call is not None
    assert all(isinstance(item, InputMediaPhoto) for item in media_call.kwargs["media"])


@pytest.mark.asyncio
async def test_telegram_errors_are_wrapped_without_raw_error_details() -> None:
    bot = Mock(spec=Bot)
    bot.send_photo = AsyncMock(side_effect=RuntimeError("untrusted response text"))
    delivery = TelegramMemeDelivery(bot)

    with pytest.raises(MemeDeliveryError, match="Unexpected Telegram media delivery failure"):
        await delivery.send_images(
            chat_id=-100123,
            topic_id=42,
            images=[_image("https://cdn.test/one.png", "image/png", ".png")],
            caption="Caption",
        )
