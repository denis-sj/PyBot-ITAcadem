"""Broadcast to all users from the main menu: ask for the text, confirm, send."""

import contextlib

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka import FromDishka

from .....domain.exceptions import BroadcastAlreadyRunningError, UsersNotFoundError
from .....dto import BroadcastDTO
from .....services.broadcast import BroadcastService
from .....services.notification_facade import NotificationFacade
from ....texts import (
    BROADCAST_ALREADY_RUNNING,
    BROADCAST_MENU_CANCELLED,
    BROADCAST_MENU_CONFIRM,
    BROADCAST_MENU_DRAFT_LOST,
    BROADCAST_MENU_PROMPT,
    BROADCAST_NO_RECIPIENTS,
)
from ...filters import check_text_message_correction, create_chat_type_routers
from ...keyboards.main_menu import (
    MainMenuCB,
    MenuAction,
    get_broadcast_cancel_keyboard,
    get_broadcast_confirm_keyboard,
)
from .broadcast_commands import notify_broadcast_result
from .states import BroadcastMenuSG

(broadcast_menu_private_router, _, _) = create_chat_type_routers("broadcast_menu")

_BROADCAST_ROLE_FLAGS = {"role_policy": "broadcast_allowed_roles"}
_BROADCAST_TEXT_KEY = "broadcast_text"


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Main menu button «Рассылка всем»: ask the administrator for the broadcast text."""
    if isinstance(callback.message, Message):
        await state.clear()
        await state.set_state(BroadcastMenuSG.waiting_for_text)
        await callback.message.answer(
            BROADCAST_MENU_PROMPT,
            parse_mode="HTML",
            reply_markup=get_broadcast_cancel_keyboard(),
        )
    await callback.answer()


@broadcast_menu_private_router.message(
    BroadcastMenuSG.waiting_for_text,
    F.text,
    ~F.text.startswith("/"),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def msg_receive_broadcast_text(message: Message, state: FSMContext) -> None:
    """Remember the drafted text and ask to confirm sending it to everybody."""
    text = check_text_message_correction(message)
    if text is None:
        return

    await state.update_data({_BROADCAST_TEXT_KEY: text})
    await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
    await message.reply(BROADCAST_MENU_CONFIRM, reply_markup=get_broadcast_confirm_keyboard())


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST_SEND),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast_send(
    callback: CallbackQuery,
    state: FSMContext,
    broadcast_service: FromDishka[BroadcastService],
    notification_facade: FromDishka[NotificationFacade],
) -> None:
    """Send the confirmed text to all users and report the result to the administrator."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    broadcast_text = await _pop_confirmed_text(state)
    await _remove_buttons(callback.message)
    await callback.answer()
    if broadcast_text is None:
        await callback.message.answer(BROADCAST_MENU_DRAFT_LOST)
        return

    try:
        result = await broadcast_service.broadcast_for_all(BroadcastDTO(broadcast_message=broadcast_text))
    except UsersNotFoundError:
        await callback.message.answer(BROADCAST_NO_RECIPIENTS)
        return
    except BroadcastAlreadyRunningError:
        await callback.message.answer(BROADCAST_ALREADY_RUNNING)
        return

    await notify_broadcast_result(
        callback.message,
        notification_facade,
        result,
        recipient_id=callback.from_user.id,
    )


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST_CANCEL),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Drop the draft and leave the broadcast scenario without sending anything."""
    if await _is_in_broadcast_scenario(state):
        await state.clear()
    if isinstance(callback.message, Message):
        await _remove_buttons(callback.message)
        await callback.message.answer(BROADCAST_MENU_CANCELLED)
    await callback.answer()


async def _pop_confirmed_text(state: FSMContext) -> str | None:
    """Return the drafted text once and clear the scenario, so a repeated press cannot send it twice."""
    if await state.get_state() != BroadcastMenuSG.waiting_for_confirmation.state:
        return None

    data = await state.get_data()
    await state.clear()
    broadcast_text = data.get(_BROADCAST_TEXT_KEY)
    if not isinstance(broadcast_text, str) or not broadcast_text.strip():
        return None
    return broadcast_text


async def _is_in_broadcast_scenario(state: FSMContext) -> bool:
    """Tell whether the user is inside this scenario, so stale buttons never reset another scenario."""
    current_state = await state.get_state()
    return current_state in {
        BroadcastMenuSG.waiting_for_text.state,
        BroadcastMenuSG.waiting_for_confirmation.state,
    }


async def _remove_buttons(message: Message) -> None:
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_reply_markup(reply_markup=None)
