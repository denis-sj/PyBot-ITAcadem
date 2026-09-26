"""Inline keyboards and callback data for attendance presentation."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ....texts.attendance import (
    BTN_CANCEL,
    BTN_CONFIRM,
    BTN_EDIT_TIME,
    BTN_EDIT_USER,
    BTN_EXCLUDE,
)


class AttendanceDraftCB(CallbackData, prefix="att_draft"):
    """Callback data for draft actions."""

    action: str
    draft_id: int


class AttendanceReportCB(CallbackData, prefix="att_rep"):
    """Callback data for report actions."""

    mode: str


def get_draft_actions_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Generate action keyboard for attendance draft preview."""
    buttons = [
        [
            InlineKeyboardButton(
                text=BTN_CONFIRM,
                callback_data=AttendanceDraftCB(action="confirm", draft_id=draft_id).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text=BTN_EDIT_TIME,
                callback_data=AttendanceDraftCB(action="edittime", draft_id=draft_id).pack(),
            ),
            InlineKeyboardButton(
                text=BTN_EDIT_USER,
                callback_data=AttendanceDraftCB(action="edituser", draft_id=draft_id).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text=BTN_EXCLUDE,
                callback_data=AttendanceDraftCB(action="exclude", draft_id=draft_id).pack(),
            ),
            InlineKeyboardButton(
                text=BTN_CANCEL,
                callback_data=AttendanceDraftCB(action="cancel", draft_id=draft_id).pack(),
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_report_keyboard() -> InlineKeyboardMarkup:
    """Generate keyboard for selecting report type."""
    buttons = [
        [
            InlineKeyboardButton(
                text="📄 Краткий отчёт",
                callback_data=AttendanceReportCB(mode="short").pack(),
            ),
            InlineKeyboardButton(
                text="📋 Подробный отчёт",
                callback_data=AttendanceReportCB(mode="detailed").pack(),
            ),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_edit_keyboard() -> InlineKeyboardMarkup:
    """Generate a single cancel button for FSM prompt."""
    buttons = [
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="att_fsm_cancel",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
