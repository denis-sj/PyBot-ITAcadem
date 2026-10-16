"""Admin Telegram commands for the weekly Lemmy meme publication."""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka

from ....services.meme_of_the_week import MemeOfTheWeekService, MemePublicationStatus

weekly_meme_router = Router(name="weekly_meme")


@weekly_meme_router.message(
    Command("meme_next"),
    F.chat.type == ChatType.PRIVATE,
    flags={"role": "Admin", "rate_limit": "moderate"},
)
async def handle_meme_next(message: Message, service: FromDishka[MemeOfTheWeekService]) -> None:
    """Replace the current weekly meme from an Admin's private chat."""
    telegram_id = message.from_user.id if message.from_user is not None else 0
    result = await service.publish_replacement(replacer_telegram_id=telegram_id)
    await message.answer(_result_text(result.status, replacement=True), parse_mode=None)


@weekly_meme_router.message(
    Command("meme_now"),
    flags={"role": "Admin", "rate_limit": "moderate"},
)
async def handle_meme_now(message: Message, service: FromDishka[MemeOfTheWeekService]) -> None:
    """Publish a test meme for an Admin without consuming the weekly slot."""
    result = await service.publish_test()
    await message.answer(_result_text(result.status, replacement=False), parse_mode=None)


def _result_text(status: MemePublicationStatus, *, replacement: bool) -> str:
    if status is MemePublicationStatus.PUBLISHED:
        return "Мем заменён." if replacement else "Тестовый мем опубликован."
    return {
        MemePublicationStatus.NO_CANDIDATES: "Больше подходящих мемов нет.",
        MemePublicationStatus.NO_CURRENT_MEME: "Мема для замены на этой неделе нет.",
        MemePublicationStatus.REPLACEMENT_LIMIT: "Лимит замен на эту неделю исчерпан.",
        MemePublicationStatus.BUSY: "Замена уже выполняется.",
        MemePublicationStatus.ALREADY_PUBLISHED: "Мем этой недели уже опубликован.",
        MemePublicationStatus.DISABLED: "Публикация мемов не настроена.",
    }.get(status, "Не удалось отправить мем.")
