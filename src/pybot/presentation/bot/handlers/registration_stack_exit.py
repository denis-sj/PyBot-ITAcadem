"""Route commands and main-menu callbacks out of registration stack onboarding."""

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager
from aiogram_dialog.api.internal import CONTEXT_KEY

from ..dialogs.user_reg.states import CreateProfileSG

registration_stack_exit_router = Router(name="registration_stack_exit")


@registration_stack_exit_router.message(F.chat.type == "private", F.text.startswith("/"))
async def exit_stack_for_command(message: Message, dialog_manager: DialogManager) -> None:
    """Close the stack dialog before an ordinary private command is routed."""
    del message
    await _close_stack_if_active(dialog_manager)
    return UNHANDLED


@registration_stack_exit_router.callback_query(F.data.startswith("menu:"))
async def exit_stack_for_menu(callback: CallbackQuery, dialog_manager: DialogManager) -> None:
    """Close the stack dialog before an ordinary main-menu action is routed."""
    del callback
    await _close_stack_if_active(dialog_manager)
    return UNHANDLED


async def _close_stack_if_active(dialog_manager: DialogManager) -> None:
    if not dialog_manager.has_context():
        return
    if dialog_manager.current_context().state not in {
        CreateProfileSG.technology_stack,
        CreateProfileSG.technology_stack_confirmation,
    }:
        return
    await dialog_manager.done()
    dialog_manager.middleware_data[CONTEXT_KEY] = None
