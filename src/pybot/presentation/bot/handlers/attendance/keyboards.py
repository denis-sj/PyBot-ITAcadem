"""Inline keyboards and callback data for attendance presentation."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ....texts.attendance import (
    BTN_CANCEL,
    BTN_CONFIRM,
    BTN_EDIT_TIME,
    BTN_EDIT_USER,
    BTN_EXCLUDE,
    BTN_TO_MONTHS,
    MONTH_NAMES_RU_CAPITALIZED,
)
from ...keyboards.navigation import get_close_button


class AttendanceDraftCB(CallbackData, prefix="att_draft"):
    """Callback data for draft actions."""

    action: str
    draft_id: int


class AttendanceReportCB(CallbackData, prefix="att_rep"):
    """Callback data for report actions."""

    mode: str


class AttendancePhotoCB(CallbackData, prefix="att_photo"):
    """Callback data for viewing attendance import photo."""

    import_id: int


def get_attendance_photo_button(import_id: int) -> InlineKeyboardButton:
    """Generate an inline button to view attendance sheet photo."""
    return InlineKeyboardButton(
        text=f"📷 Фото импорта #{import_id}",
        callback_data=AttendancePhotoCB(import_id=import_id).pack(),
    )


MY_VISITS_PAGE_SIZE: int = 10
MY_VISITS_BUTTONS_PER_ROW: int = 2


class MyVisitsCB(CallbackData, prefix="my_vis"):
    """Callback data for personal visits multi-month navigation."""

    action: str  # "month", "months", "page"
    year: int = 0
    month: int = 0
    page: int = 0


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
        ],
        [
            get_close_button(),
        ],
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


def get_my_visits_months_keyboard(
    months: list[tuple[int, int]],
    page: int = 0,
    page_size: int = MY_VISITS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    """Generate keyboard for selecting visit month with pagination and close button."""
    start = page * page_size
    end = start + page_size
    page_months = months[start:end]

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for y, m in page_months:
        m_name = MONTH_NAMES_RU_CAPITALIZED.get(m, f"Месяц {m}")
        btn = InlineKeyboardButton(
            text=f"{m_name} {y}",
            callback_data=MyVisitsCB(action="month", year=y, month=m, page=page).pack(),
        )
        row.append(btn)
        if len(row) == MY_VISITS_BUTTONS_PER_ROW:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)

    total_pages = (len(months) + page_size - 1) // page_size
    nav_row: list[InlineKeyboardButton] = []
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="← Раньше",
                callback_data=MyVisitsCB(action="page", page=page + 1).pack(),
            )
        )
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="Позже →",
                callback_data=MyVisitsCB(action="page", page=page - 1).pack(),
            )
        )
    if nav_row:
        keyboard_rows.append(nav_row)

    keyboard_rows.append([get_close_button()])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_my_visits_month_report_keyboard(
    unique_import_ids: list[int],
    has_multiple_months: bool,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Generate keyboard for month visit report with photo buttons and back/close navigation."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for imp_id in unique_import_ids:
        keyboard_rows.append([get_attendance_photo_button(imp_id)])

    if has_multiple_months:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_TO_MONTHS,
                    callback_data=MyVisitsCB(action="months", page=page).pack(),
                )
            ]
        )
    else:
        keyboard_rows.append([get_close_button()])

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
