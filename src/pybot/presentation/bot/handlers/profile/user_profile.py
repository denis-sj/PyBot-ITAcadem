"""Модуль бота IT Academ."""

from aiogram import Bot, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager
from aiogram_dialog.api.entities.modes import StartMode
from dishka.integrations.aiogram import FromDishka

from .....core.config import AppSettings
from .....dto import UserReadDTO
from .....services.user_services import UserProfileService, UserService
from ....texts import PROFILE_GROUP_REGISTRATION_REQUIRED, render_profile_message
from ...dialogs.user_reg.states import CreateProfileSG
from ...filters import create_chat_type_routers
from ...keyboards.main_menu import MainMenuCB, MenuAction, get_main_menu_keyboard
from ...utils.menu_access import can_use_broadcast_menu
from ...utils.registration_prompt import answer_registration_required

user_profile_private_router, user_profile_group_router, user_profile_global_router = create_chat_type_routers(
    "user_profile"
)


# /profile - в личном чате
@user_profile_private_router.message(Command("profile"))
async def cmd_profile_private(
    message: Message,
    dialog_manager: DialogManager,
    user_service: FromDishka[UserService],
    user_profile_service: FromDishka[UserProfileService],
) -> None:
    """Обработчик команды /profile_private."""
    if message.from_user:
        user = await user_service.find_user_by_telegram_id(message.from_user.id)
        if user:
            await _answer_profile(message, user, user_profile_service)
            return

    await dialog_manager.start(CreateProfileSG.welcome, mode=StartMode.RESET_STACK)


# /profile - в группе или супергруппе
@user_profile_group_router.message(Command("profile"))
async def cmd_profile_group(
    message: Message,
    user_service: FromDishka[UserService],
    user_profile_service: FromDishka[UserProfileService],
    bot: Bot,
) -> None:
    """Обработчик публичного self-profile в группах."""
    if not message.from_user:
        await message.answer(PROFILE_GROUP_REGISTRATION_REQUIRED)
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if not user:
        await answer_registration_required(message, bot, text=PROFILE_GROUP_REGISTRATION_REQUIRED, parse_mode=None)
        return

    await _answer_profile(message, user, user_profile_service)


# Кнопка «Профиль» главного меню - обновляет карточку профиля на месте
@user_profile_private_router.callback_query(MainMenuCB.filter(F.action == MenuAction.PROFILE))
async def cb_menu_profile(  # noqa: PLR0913
    callback: CallbackQuery,
    user_service: FromDishka[UserService],
    user_profile_service: FromDishka[UserProfileService],
    settings: FromDishka[AppSettings],
    bot: Bot,
    user_roles: set[str] | None = None,
) -> None:
    """Кнопка меню «Профиль»: показывает актуальный профиль или предлагает пройти регистрацию."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await answer_registration_required(callback.message, bot)
        await callback.answer()
        return

    user_profile_dto = await user_profile_service.build_profile_view(user)
    menu = get_main_menu_keyboard(show_broadcast=can_use_broadcast_menu(user_roles, settings))
    try:
        await callback.message.edit_text(render_profile_message(user_profile_dto), parse_mode="HTML", reply_markup=menu)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            await callback.message.answer(
                render_profile_message(user_profile_dto), parse_mode="HTML", reply_markup=menu
            )
    await callback.answer()


async def _answer_profile(
    message: Message,
    user: UserReadDTO,
    user_profile_service: UserProfileService,
) -> None:
    user_profile_dto = await user_profile_service.build_profile_view(user)
    await message.answer(render_profile_message(user_profile_dto), parse_mode="HTML")
