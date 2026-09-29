"""Broadcast to all users from the main menu: ask for the text, confirm, send."""

import contextlib
from uuid import uuid4

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
    BROADCAST_MENU_ALREADY_RUNNING,
    BROADCAST_MENU_CANCELLED,
    BROADCAST_MENU_CONFIRM,
    BROADCAST_MENU_DRAFT_OUTDATED,
    BROADCAST_MENU_IN_PROGRESS,
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
_BROADCAST_DRAFT_ID_KEY = "broadcast_draft_id"
_DRAFT_ID_LENGTH = 8


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Main menu button «Рассылка всем»: ask the administrator for the broadcast text."""
    if await state.get_state() == BroadcastMenuSG.sending.state:
        await callback.answer(BROADCAST_MENU_IN_PROGRESS)
        return

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
    """Remember the drafted text under a new draft ID and ask to confirm sending it to everybody."""
    text = check_text_message_correction(message)
    if text is None:
        return

    draft_id = uuid4().hex[:_DRAFT_ID_LENGTH]
    await state.update_data({_BROADCAST_TEXT_KEY: text, _BROADCAST_DRAFT_ID_KEY: draft_id})
    await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
    await message.reply(BROADCAST_MENU_CONFIRM, reply_markup=get_broadcast_confirm_keyboard(draft_id))


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST_SEND),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast_send(
    callback: CallbackQuery,
    callback_data: MainMenuCB,
    state: FSMContext,
    broadcast_service: FromDishka[BroadcastService],
    notification_facade: FromDishka[NotificationFacade],
) -> None:
    """Send the confirmed draft to all users and report the result to the administrator.

    The draft stays in the FSM while the sending is in progress and is dropped only when the broadcast
    is done, so the administrator can retry after «another broadcast is already running».
    """
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if await state.get_state() == BroadcastMenuSG.sending.state:
        await callback.answer(BROADCAST_MENU_IN_PROGRESS)
        return

    broadcast_text = await _start_sending_draft(state, callback_data.draft)
    await _remove_buttons(callback.message)
    await callback.answer()
    if broadcast_text is None:
        await callback.message.answer(BROADCAST_MENU_DRAFT_OUTDATED)
        return

    try:
        result = await broadcast_service.broadcast_for_all(BroadcastDTO(broadcast_message=broadcast_text))
    except UsersNotFoundError:
        await _finish_sending(state)
        await callback.message.answer(BROADCAST_NO_RECIPIENTS)
        return
    except BroadcastAlreadyRunningError:
        await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
        await callback.message.answer(
            BROADCAST_MENU_ALREADY_RUNNING,
            reply_markup=get_broadcast_confirm_keyboard(callback_data.draft),
        )
        return
    except Exception:
        await _finish_sending(state)
        raise

    await _finish_sending(state)
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
async def cb_menu_broadcast_cancel(callback: CallbackQuery, callback_data: MainMenuCB, state: FSMContext) -> None:
    """Drop the draft and leave the broadcast scenario without sending anything."""
    is_cancelled = await _cancel_scenario(state, callback_data.draft)
    if isinstance(callback.message, Message):
        await _remove_buttons(callback.message)
        await callback.message.answer(BROADCAST_MENU_CANCELLED if is_cancelled else BROADCAST_MENU_DRAFT_OUTDATED)
    await callback.answer()


async def _start_sending_draft(state: FSMContext, draft_id: str) -> str | None:
    """Return the text of the confirmed draft and mark it as being sent, or ``None`` for a stale button.

    A button belongs to one draft: it works only while that very draft is waiting for the confirmation.
    Moving to the ``sending`` state right away makes a repeated press harmless.
    """
    if not await _is_waiting_for_confirmation_of(state, draft_id):
        return None

    broadcast_text = (await state.get_data()).get(_BROADCAST_TEXT_KEY)
    if not isinstance(broadcast_text, str) or not broadcast_text.strip():
        return None

    await state.set_state(BroadcastMenuSG.sending)
    return broadcast_text


async def _cancel_scenario(state: FSMContext, draft_id: str) -> bool:
    """Leave the scenario; a draft button cancels only its own draft, the prompt button cancels the scenario."""
    if draft_id:
        is_cancellable = await _is_waiting_for_confirmation_of(state, draft_id)
    else:
        is_cancellable = await state.get_state() in {
            BroadcastMenuSG.waiting_for_text.state,
            BroadcastMenuSG.waiting_for_confirmation.state,
        }

    if is_cancellable:
        await state.clear()
    return is_cancellable


async def _is_waiting_for_confirmation_of(state: FSMContext, draft_id: str) -> bool:
    if not draft_id or await state.get_state() != BroadcastMenuSG.waiting_for_confirmation.state:
        return False
    return (await state.get_data()).get(_BROADCAST_DRAFT_ID_KEY) == draft_id


async def _finish_sending(state: FSMContext) -> None:
    """Drop the draft once its sending is over, without touching a scenario the user has moved on to."""
    if await state.get_state() == BroadcastMenuSG.sending.state:
        await state.clear()


async def _remove_buttons(message: Message) -> None:
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_reply_markup(reply_markup=None)
