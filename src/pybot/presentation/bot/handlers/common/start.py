"""Модуль бота IT Academ."""

from aiogram import F, flags
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager
from aiogram_dialog.api.entities.modes import ShowMode, StartMode
from dishka.integrations.aiogram import FromDishka

from .....core.config import AppSettings
from .....dto import UserReadDTO
from .....services.user_services import UserProfileService, UserRolesService, UserService
from .....utils import has_any_role
from ....texts import (
    HELP_GROUP,
    HELP_PRIVATE,
    HELP_PRIVATE_PUBLIC,
    INFO_GLOBAL,
    START_GROUP_GREETING,
    START_USER_ERROR,
    render_profile_message,
)
from ...dialogs.user_reg.states import CreateProfileSG
from ...filters import create_chat_type_routers
from ...keyboards.main_menu import MainMenuCB, MenuAction, get_main_menu_keyboard
from ...utils.menu_access import can_use_broadcast_menu

start_private_router, start_group_router, start_global_router = create_chat_type_routers("start")


@start_private_router.message(CommandStart())
@flags.public(True)
async def cmd_start_private(  # noqa: PLR0913
    message: Message,
    dialog_manager: DialogManager,
    user_service: FromDishka[UserService],
    user_profile_service: FromDishka[UserProfileService],
    settings: FromDishka[AppSettings],
    user_roles: set[str] | None = None,
) -> None:
    """Обработчик команды /start_private: профиль с главным меню или регистрация."""
    if not message.from_user:
        await message.answer(START_USER_ERROR)
        return

    user = await user_service.find_user_by_telegram_id(message.from_user.id)
    if user:
        show_broadcast = can_use_broadcast_menu(user_roles, settings)
        await _answer_profile_with_menu(message, user, user_profile_service, show_broadcast=show_broadcast)
        return

    await dialog_manager.start(CreateProfileSG.welcome, mode=StartMode.RESET_STACK)


@start_global_router.message(CommandStart())
async def cmd_start_group(message: Message) -> None:
    """Обработчик команды /start_group."""
    await message.answer(START_GROUP_GREETING)


@start_global_router.message(Command("info"))
async def cmd_info(message: Message) -> None:
    """Обработчик команды /info."""
    await message.answer(INFO_GLOBAL)


@start_private_router.message(Command("help"))
async def cmd_help_private(
    message: Message,
    user_roles_service: FromDishka[UserRolesService],
    user_id: int | None = None,
) -> None:
    """Обработчик команды /help_private."""
    await message.answer(await _resolve_private_help_text(user_roles_service, user_id))


@start_private_router.callback_query(MainMenuCB.filter(F.action == MenuAction.HELP))
async def cb_menu_help(
    callback: CallbackQuery,
    user_roles_service: FromDishka[UserRolesService],
    user_id: int | None = None,
) -> None:
    """Кнопка меню «Помощь»: отправляет тот же текст, что и /help в личном чате."""
    if isinstance(callback.message, Message):
        await callback.message.answer(await _resolve_private_help_text(user_roles_service, user_id))
    await callback.answer()


@start_private_router.callback_query(MainMenuCB.filter(F.action == MenuAction.REGISTER))
async def cb_menu_register(  # noqa: PLR0913
    callback: CallbackQuery,
    dialog_manager: DialogManager,
    user_service: FromDishka[UserService],
    user_profile_service: FromDishka[UserProfileService],
    settings: FromDishka[AppSettings],
    user_roles: set[str] | None = None,
) -> None:
    """Кнопка «Начать регистрацию»: запускает диалог регистрации или показывает профиль, если он уже есть."""
    user = await user_service.find_user_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer()
        await dialog_manager.start(CreateProfileSG.welcome, mode=StartMode.RESET_STACK, show_mode=ShowMode.SEND)
        return

    if isinstance(callback.message, Message):
        show_broadcast = can_use_broadcast_menu(user_roles, settings)
        await _answer_profile_with_menu(callback.message, user, user_profile_service, show_broadcast=show_broadcast)
    await callback.answer()


@start_group_router.message(Command("help"))
async def cmd_help_group(message: Message) -> None:
    """Обработчик команды /help_group."""
    await message.answer(HELP_GROUP)


async def _resolve_private_help_text(user_roles_service: UserRolesService, user_id: int | None) -> str:
    if user_id is None:
        return HELP_PRIVATE_PUBLIC

    user_roles = await user_roles_service.find_user_roles(user_id)
    if user_roles and has_any_role(set(user_roles), {"Admin"}):
        return HELP_PRIVATE
    return HELP_PRIVATE_PUBLIC


async def _answer_profile_with_menu(
    message: Message,
    user: UserReadDTO,
    user_profile_service: UserProfileService,
    *,
    show_broadcast: bool,
) -> None:
    user_profile_dto = await user_profile_service.build_profile_view(user)
    menu = get_main_menu_keyboard(show_broadcast=show_broadcast)
    await message.answer(render_profile_message(user_profile_dto), parse_mode="HTML", reply_markup=menu)
