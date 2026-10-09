"""Модуль бота IT Academ."""

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka

from .....core import logger
from .....core.constants import PointsTypeEnum
from .....services import LeaderboardService
from ....texts import LEADERBOARD_UNEXPECTED_ERROR, render_leaderboard_message
from ...filters import create_chat_type_routers
from ...keyboards.main_menu import MainMenuCB, MenuAction

(_, _, leaderboard_global_router) = create_chat_type_routers("leaderboard")


@leaderboard_global_router.message(Command("leaderboard"), flags={"rate_limit": "moderate"})
async def handle_leaderboard(
    message: Message,
    leaderboard_service: FromDishka[LeaderboardService],
) -> None:
    """Форматирует текст для таблицы лидеров."""
    text = await _build_leaderboard_text(leaderboard_service)
    if text is None:
        await message.answer(LEADERBOARD_UNEXPECTED_ERROR)
        return

    await message.answer(text, parse_mode="HTML")


@leaderboard_global_router.callback_query(MainMenuCB.filter(F.action == MenuAction.LEADERBOARD))
async def cb_menu_leaderboard(
    callback: CallbackQuery,
    leaderboard_service: FromDishka[LeaderboardService],
) -> None:
    """Кнопка меню «Лидерборд»: отправляет тот же текст, что и /leaderboard."""
    if isinstance(callback.message, Message):
        text = await _build_leaderboard_text(leaderboard_service)
        if text is None:
            await callback.message.answer(LEADERBOARD_UNEXPECTED_ERROR)
        else:
            await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


async def _build_leaderboard_text(leaderboard_service: LeaderboardService) -> str | None:
    """Return the leaderboard message or ``None`` when the data could not be loaded."""
    try:
        period = leaderboard_service.get_previous_calendar_week_period()
        academic_rows = await leaderboard_service.get_previous_calendar_week_leaderboard(
            points_type=PointsTypeEnum.ACADEMIC,
        )
        reputation_rows = await leaderboard_service.get_previous_calendar_week_leaderboard(
            points_type=PointsTypeEnum.REPUTATION,
        )
    except Exception:
        logger.exception("Unexpected error while loading leaderboard")
        return None

    return render_leaderboard_message(
        academic_rows=academic_rows,
        reputation_rows=reputation_rows,
        period=period,
    )
