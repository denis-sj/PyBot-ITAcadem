"""Broadcast to all users from the main menu: ask for the text, confirm, send."""

import asyncio
import contextlib
from uuid import uuid4

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka import FromDishka

from .....core import logger
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
    BROADCAST_MENU_INTERRUPTED,
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

# Administrators whose confirmed draft is being sent by this bot process right now.
#
# The FSM state alone cannot keep one draft from being sent twice: reading it and switching it to ``sending``
# are separate awaits, and every update is handled in its own task, so parallel presses can both pass the check.
# The claim is taken without an ``await`` between the check and the ``add``, which makes it atomic for the event
# loop. The broadcast lock of ``BroadcastService`` is process-local as well, so a process-local claim is enough.
#
# The set is empty after a restart, which is what tells a live ``sending`` state from an orphaned one.
_sending_admins: set[int] = set()


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Main menu button «Рассылка всем»: ask the administrator for the broadcast text."""
    if await _reject_while_sending(callback, state):
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

    One administrator can have only one sending in flight: of several parallel presses exactly one takes the
    claim and the others are told that the broadcast is already being sent.
    """
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    admin_id = callback.from_user.id
    if admin_id in _sending_admins:
        await callback.answer(BROADCAST_MENU_IN_PROGRESS)
        return

    _sending_admins.add(admin_id)  # no await since the check above: the claim is atomic
    try:
        await _send_confirmed_draft(
            callback,
            message,
            callback_data.draft,
            state,
            broadcast_service,
            notification_facade,
        )
    finally:
        _sending_admins.discard(admin_id)


@broadcast_menu_private_router.callback_query(
    MainMenuCB.filter(F.action == MenuAction.BROADCAST_CANCEL),
    flags=_BROADCAST_ROLE_FLAGS,
)
async def cb_menu_broadcast_cancel(callback: CallbackQuery, callback_data: MainMenuCB, state: FSMContext) -> None:
    """Drop the draft and leave the broadcast scenario without sending anything."""
    if await _reject_while_sending(callback, state):
        return

    is_cancelled = await _cancel_scenario(state, callback_data.draft)
    if isinstance(callback.message, Message):
        await _remove_buttons(callback.message)
        await callback.message.answer(BROADCAST_MENU_CANCELLED if is_cancelled else BROADCAST_MENU_DRAFT_OUTDATED)
    await callback.answer()


async def _send_confirmed_draft(  # noqa: PLR0913
    callback: CallbackQuery,
    message: Message,
    draft_id: str,
    state: FSMContext,
    broadcast_service: BroadcastService,
    notification_facade: NotificationFacade,
) -> None:
    """Send the draft of the pressed button; the caller holds the administrator's sending claim.

    The draft stays in the FSM while the sending is in progress and is dropped only when the broadcast is over,
    so the administrator can retry after «another broadcast is already running».
    """
    if await state.get_state() == BroadcastMenuSG.sending.state:
        # The claim is ours, so no live handler owns this state: it is a leftover of an interrupted sending.
        await state.clear()
        await _remove_buttons(message)
        await callback.answer(BROADCAST_MENU_INTERRUPTED, show_alert=True)
        return

    broadcast_text = await _start_sending_draft(state, draft_id)
    await _remove_buttons(message)
    await callback.answer()
    if broadcast_text is None:
        await message.answer(BROADCAST_MENU_DRAFT_OUTDATED)
        return

    try:
        result = await broadcast_service.broadcast_for_all(BroadcastDTO(broadcast_message=broadcast_text))
    except UsersNotFoundError:
        await _finish_sending(state)
        await message.answer(BROADCAST_NO_RECIPIENTS)
        return
    except BroadcastAlreadyRunningError:
        await state.set_state(BroadcastMenuSG.waiting_for_confirmation)
        await message.answer(BROADCAST_MENU_ALREADY_RUNNING, reply_markup=get_broadcast_confirm_keyboard(draft_id))
        return
    except asyncio.CancelledError:
        # Part of the messages may already be delivered, so the draft is not kept for a retry: it would duplicate them.
        await asyncio.shield(_abandon_interrupted_sending(state, message))
        raise
    except Exception:
        await _finish_sending(state)
        raise

    await _finish_sending(state)
    await notify_broadcast_result(message, notification_facade, result, recipient_id=callback.from_user.id)


async def _reject_while_sending(callback: CallbackQuery, state: FSMContext) -> bool:
    """Answer the press and return ``True`` if the scenario is busy with a sending that must not be disturbed.

    A live sending is reported as «in progress». A ``sending`` state that no running handler owns is a leftover of
    an interrupted sending (the bot restarted, for example): it is dropped here, so that it does not lock the
    scenario for good, and the administrator is told that part of the messages may have been delivered.
    """
    admin_id = callback.from_user.id
    if admin_id not in _sending_admins and await state.get_state() != BroadcastMenuSG.sending.state:
        return False

    if admin_id in _sending_admins:  # checked again: the sending may have started while the state was being read
        await callback.answer(BROADCAST_MENU_IN_PROGRESS)
    else:
        await state.clear()
        await callback.answer(BROADCAST_MENU_INTERRUPTED, show_alert=True)
    return True


async def _start_sending_draft(state: FSMContext, draft_id: str) -> str | None:
    """Return the text of the confirmed draft and mark it as being sent, or ``None`` for a stale button.

    A button belongs to one draft: it works only while that very draft is waiting for the confirmation.
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


async def _abandon_interrupted_sending(state: FSMContext, message: Message) -> None:
    """Free the scenario after a cancelled sending and warn that part of the messages may be delivered.

    It runs while the handler is being cancelled, so nothing here may raise: the cancellation goes on afterwards.
    """
    logger.warning("event=broadcast_menu phase=sending status=cancelled chat_id={chat_id}", chat_id=message.chat.id)
    try:
        await _finish_sending(state)
    except Exception:
        logger.exception("event=broadcast_menu phase=cancelled_cleanup status=state_reset_failed")
    try:
        await message.answer(BROADCAST_MENU_INTERRUPTED)
    except Exception:
        logger.exception("event=broadcast_menu phase=cancelled_cleanup status=notification_failed")


async def _remove_buttons(message: Message) -> None:
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_reply_markup(reply_markup=None)
