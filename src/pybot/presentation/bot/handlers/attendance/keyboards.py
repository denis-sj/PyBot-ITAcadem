"""Inline keyboards and callback data for attendance presentation."""

from collections.abc import Sequence
from datetime import date, time
from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ....texts.attendance import (
    BTN_ADD_VISIT,
    BTN_BACK,
    BTN_CANCEL,
    BTN_CONFIRM,
    BTN_DELETE_VISIT,
    BTN_EDIT_TIME,
    BTN_EDIT_USER,
    BTN_EDIT_VISIT_TIME,
    BTN_EXCLUDE,
    BTN_TO_DATES,
    BTN_TO_MONTHS,
    BTN_TO_USERS,
    BTN_VISITS_MONTH_TOP,
    BTN_VISITS_MY,
    BTN_VISITS_WEEK_TOP,
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


class AttendanceUploadCB(CallbackData, prefix="att_upload"):
    """Callback data for starting attendance photo upload."""

    action: str


class AttendancePhotoCB(CallbackData, prefix="att_photo"):
    """Callback data for viewing attendance import photo.

    ``owner`` is the Telegram ID of the user who may press the button in a shared chat;
    0 means the button is not restricted to a single user.
    """

    import_id: int
    owner: int = 0


class AttendancePhotoBackCB(CallbackData, prefix="att_photo_back"):
    """Callback data for returning from a photo to the previous screen.

    ``owner`` is the Telegram ID of the only user who may remove the photo; 0 means anyone.
    """

    action: str = "back"
    owner: int = 0


class AttendancePhotoBrowseCB(CallbackData, prefix="att_photo_browse"):
    """Callback data for month selection in /attendance_photo."""

    action: str
    year: int = 0
    month: int = 0


def get_attendance_photo_button(import_id: int, owner_id: int = 0) -> InlineKeyboardButton:
    """Generate an inline button to view attendance sheet photo."""
    return InlineKeyboardButton(
        text=f"📷 Номер фото #{import_id}",
        callback_data=AttendancePhotoCB(import_id=import_id, owner=owner_id).pack(),
        style="primary",
    )


def get_attendance_photo_back_keyboard(owner_id: int = 0) -> InlineKeyboardMarkup:
    """Generate a button that removes the opened photo and reveals the previous screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_BACK,
                    callback_data=AttendancePhotoBackCB(owner=owner_id).pack(),
                    style="primary",
                )
            ]
        ]
    )


def get_attendance_photo_months_keyboard(months: list[tuple[int, int]]) -> InlineKeyboardMarkup:
    """Generate an inline keyboard for months that have confirmed photo imports."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for year, month in months:
        month_name = MONTH_NAMES_RU_CAPITALIZED.get(month, f"Месяц {month}")
        row.append(
            InlineKeyboardButton(
                text=f"{month_name} {year}",
                callback_data=AttendancePhotoBrowseCB(action="month", year=year, month=month).pack(),
                style="primary",
            )
        )
        if len(row) == MY_VISITS_BUTTONS_PER_ROW:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_attendance_photo_dates_keyboard(
    dates: list[tuple[date, int]],
) -> InlineKeyboardMarkup:
    """Generate recorded date buttons and a return-to-months button."""
    keyboard_rows = [
        [
            InlineKeyboardButton(
                text=f"{visit_date:%d.%m.%Y} · Номер фото #{import_id}",
                callback_data=AttendancePhotoCB(import_id=import_id).pack(),
                style="primary",
            )
        ]
        for visit_date, import_id in dates
    ]
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="← К месяцам",
                callback_data=AttendancePhotoBrowseCB(action="months").pack(),
                style="primary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


MY_VISITS_PAGE_SIZE: int = 10
MY_VISITS_BUTTONS_PER_ROW: int = 2


class VisitsHubCB(CallbackData, prefix="vis_hub"):
    """Callback data for the screens of the /visits card.

    ``owner`` is the Telegram ID of the user who opened the card; only this user may use it.
    """

    action: str  # "home", "week", "top_month", "my"
    year: int = 0
    month: int = 0
    owner: int = 0


class MyVisitsCB(CallbackData, prefix="my_vis"):
    """Callback data for personal visits multi-month navigation inside the /visits card."""

    action: str  # "month", "months", "page"
    year: int = 0
    month: int = 0
    page: int = 0
    owner: int = 0


def get_visits_home_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    """Generate the main /visits keyboard: weekly top, monthly top, own visits and close."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_VISITS_WEEK_TOP,
                    callback_data=VisitsHubCB(action="week", owner=owner_id).pack(),
                    style="primary",
                ),
                InlineKeyboardButton(
                    text=BTN_VISITS_MONTH_TOP,
                    callback_data=VisitsHubCB(action="top_month", owner=owner_id).pack(),
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=BTN_VISITS_MY,
                    callback_data=VisitsHubCB(action="my", owner=owner_id).pack(),
                    style="primary",
                )
            ],
            [get_close_button(owner_id=owner_id)],
        ]
    )


def get_visits_back_button(owner_id: int) -> InlineKeyboardButton:
    """Generate the button that returns from any /visits screen to the main card."""
    return InlineKeyboardButton(
        text=BTN_BACK,
        callback_data=VisitsHubCB(action="home", owner=owner_id).pack(),
        style="primary",
    )


def get_visits_back_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    """Generate a keyboard with only the back button, for the weekly top and empty screens."""
    return InlineKeyboardMarkup(inline_keyboard=[[get_visits_back_button(owner_id)]])


def get_visits_month_top_keyboard(
    owner_id: int,
    previous_month: tuple[int, int] | None,
    next_month: tuple[int, int] | None,
) -> InlineKeyboardMarkup:
    """Generate the monthly top keyboard with arrows to neighbouring months that have visits."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if previous_month is not None:
        year, month = previous_month
        nav_row.append(
            InlineKeyboardButton(
                text=f"← {MONTH_NAMES_RU_CAPITALIZED.get(month, month)} {year}",
                callback_data=VisitsHubCB(action="top_month", year=year, month=month, owner=owner_id).pack(),
                style="primary",
            )
        )
    if next_month is not None:
        year, month = next_month
        nav_row.append(
            InlineKeyboardButton(
                text=f"{MONTH_NAMES_RU_CAPITALIZED.get(month, month)} {year} →",
                callback_data=VisitsHubCB(action="top_month", year=year, month=month, owner=owner_id).pack(),
                style="primary",
            )
        )
    if nav_row:
        keyboard_rows.append(nav_row)
    keyboard_rows.append([get_visits_back_button(owner_id)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


EDIT_VISITS_PAGE_SIZE: int = 40


class EditVisitsCB(CallbackData, prefix="ed_vis"):
    """Callback data for /edit_visits navigation."""

    action: str
    user_id: int = 0
    year: int = 0
    month: int = 0
    record_id: int = 0
    page: int = 0
    target: str | None = None


def get_draft_actions_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Generate action keyboard for attendance draft preview."""
    buttons = [
        [
            InlineKeyboardButton(
                text=BTN_CONFIRM,
                callback_data=AttendanceDraftCB(action="confirm", draft_id=draft_id).pack(),
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                text=BTN_EDIT_TIME,
                callback_data=AttendanceDraftCB(action="edittime", draft_id=draft_id).pack(),
                style="primary",
            ),
            InlineKeyboardButton(
                text=BTN_EDIT_USER,
                callback_data=AttendanceDraftCB(action="edituser", draft_id=draft_id).pack(),
                style="primary",
            ),
        ],
        [
            InlineKeyboardButton(
                text=BTN_EXCLUDE,
                callback_data=AttendanceDraftCB(action="exclude", draft_id=draft_id).pack(),
                style="danger",
            ),
            InlineKeyboardButton(
                text=BTN_CANCEL,
                callback_data=AttendanceDraftCB(action="cancel", draft_id=draft_id).pack(),
                style="danger",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_report_keyboard() -> InlineKeyboardMarkup:
    """Generate the attendance menu with photo import and report actions."""
    buttons = [
        [
            InlineKeyboardButton(
                text="📸 Добавить часы по фото",
                callback_data=AttendanceUploadCB(action="photo").pack(),
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                text="📄 Краткий отчёт",
                callback_data=AttendanceReportCB(mode="short").pack(),
                style="primary",
            ),
            InlineKeyboardButton(
                text="📋 Подробный отчёт",
                callback_data=AttendanceReportCB(mode="detailed").pack(),
                style="primary",
            ),
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
                style="danger",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_my_visits_months_keyboard(
    months: list[tuple[int, int]],
    owner_id: int = 0,
    page: int = 0,
    page_size: int = MY_VISITS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    """Generate keyboard for selecting visit month with pagination and a back button to the /visits card."""
    start = page * page_size
    end = start + page_size
    page_months = months[start:end]

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for y, m in page_months:
        m_name = MONTH_NAMES_RU_CAPITALIZED.get(m, f"Месяц {m}")
        btn = InlineKeyboardButton(
            text=f"{m_name} {y}",
            callback_data=MyVisitsCB(action="month", year=y, month=m, page=page, owner=owner_id).pack(),
            style="primary",
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
                callback_data=MyVisitsCB(action="page", page=page + 1, owner=owner_id).pack(),
                style="primary",
            )
        )
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="Позже →",
                callback_data=MyVisitsCB(action="page", page=page - 1, owner=owner_id).pack(),
                style="primary",
            )
        )
    if nav_row:
        keyboard_rows.append(nav_row)

    keyboard_rows.append([get_visits_back_button(owner_id)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_my_visits_month_report_keyboard(
    unique_import_ids: list[int],
    has_multiple_months: bool,
    page: int = 0,
    owner_id: int = 0,
) -> InlineKeyboardMarkup:
    """Generate keyboard for month visit report with photo buttons and back navigation."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for imp_id in unique_import_ids:
        keyboard_rows.append([get_attendance_photo_button(imp_id, owner_id=owner_id)])

    if has_multiple_months:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_TO_MONTHS,
                    callback_data=MyVisitsCB(action="months", page=page, owner=owner_id).pack(),
                    style="primary",
                )
            ]
        )
    else:
        keyboard_rows.append([get_visits_back_button(owner_id)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_edit_visits_users_keyboard(
    total_users: int,
    page: int = 0,
    page_size: int = EDIT_VISITS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    """Generate pagination keyboard for Screen 1."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    total_pages = (total_users + page_size - 1) // page_size if total_users > 0 else 1
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="←",
                callback_data=EditVisitsCB(action="page", page=page - 1).pack(),
                style="primary",
            )
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="→",
                callback_data=EditVisitsCB(action="page", page=page + 1).pack(),
                style="primary",
            )
        )
    if nav_row:
        keyboard_rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_edit_visits_user_keyboard(
    user_id: int,
    months: list[tuple[int, int]],
) -> InlineKeyboardMarkup:
    """Generate keyboard for Screen 2: months with visits, add button, and back button."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    row: list[InlineKeyboardButton] = []
    for y, m in months:
        m_name = MONTH_NAMES_RU_CAPITALIZED.get(m, f"Месяц {m}")
        btn = InlineKeyboardButton(
            text=f"{m_name} {y}",
            callback_data=EditVisitsCB(action="month", user_id=user_id, year=y, month=m).pack(),
            style="primary",
        )
        row.append(btn)
        if len(row) == MY_VISITS_BUTTONS_PER_ROW:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=BTN_ADD_VISIT,
                callback_data=EditVisitsCB(action="start_manual", user_id=user_id).pack(),
                style="success",
            )
        ]
    )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=BTN_TO_USERS,
                callback_data=EditVisitsCB(action="users").pack(),
                style="primary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_edit_visits_month_dates_keyboard(
    user_id: int,
    year: int,
    month: int,
    records: Sequence[Any],
) -> InlineKeyboardMarkup:
    """Generate keyboard for Screen 3: buttons for each visit and back button."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    sorted_records = sorted(records, key=lambda r: (r.date, r.start_time or time.min))
    row: list[InlineKeyboardButton] = []
    for r in sorted_records:
        d_str = r.date.strftime("%d.%m")
        t_str = (
            f"{r.start_time.strftime('%H:%M')}–{r.end_time.strftime('%H:%M')}"
            if r.start_time and r.end_time
            else "без времени"
        )
        btn = InlineKeyboardButton(
            text=f"{d_str} · {t_str}",
            callback_data=EditVisitsCB(
                action="record",
                user_id=user_id,
                year=year,
                month=month,
                record_id=r.id,
            ).pack(),
            style="primary",
        )
        row.append(btn)
        if len(row) == MY_VISITS_BUTTONS_PER_ROW:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=BTN_TO_MONTHS,
                callback_data=EditVisitsCB(action="user", user_id=user_id).pack(),
                style="primary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_edit_visits_record_keyboard(
    user_id: int,
    year: int,
    month: int,
    record: Any,
) -> InlineKeyboardMarkup:
    """Generate keyboard for Screen 4: photo button, edit/delete actions, and back button."""
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    if getattr(record, "import_id", None):
        keyboard_rows.append([get_attendance_photo_button(record.import_id)])

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=BTN_EDIT_VISIT_TIME,
                callback_data=EditVisitsCB(
                    action="start_edit_time",
                    user_id=user_id,
                    year=year,
                    month=month,
                    record_id=record.id,
                ).pack(),
                style="primary",
            ),
            InlineKeyboardButton(
                text=BTN_DELETE_VISIT,
                callback_data=EditVisitsCB(
                    action="prompt_delete",
                    user_id=user_id,
                    year=year,
                    month=month,
                    record_id=record.id,
                ).pack(),
                style="danger",
            ),
        ]
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text=BTN_TO_DATES,
                callback_data=EditVisitsCB(action="month", user_id=user_id, year=year, month=month).pack(),
                style="primary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_edit_visits_cancel_keyboard(
    action: str,
    user_id: int,
    year: int = 0,
    month: int = 0,
    record_id: int = 0,
) -> InlineKeyboardMarkup:
    """Generate a single cancel button returning to the previous screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=EditVisitsCB(
                        action=action,
                        user_id=user_id,
                        year=year,
                        month=month,
                        record_id=record_id,
                    ).pack(),
                    style="danger",
                )
            ]
        ]
    )


def get_edit_visits_confirm_time_keyboard(
    user_id: int,
    year: int,
    month: int,
    record_id: int,
) -> InlineKeyboardMarkup:
    """Generate keyboard with Save and Cancel buttons for time modification."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Сохранить",
                    callback_data=EditVisitsCB(
                        action="save_time",
                        user_id=user_id,
                        year=year,
                        month=month,
                        record_id=record_id,
                    ).pack(),
                    style="success",
                ),
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=EditVisitsCB(
                        action="record",
                        user_id=user_id,
                        year=year,
                        month=month,
                        record_id=record_id,
                    ).pack(),
                    style="danger",
                ),
            ]
        ]
    )


def get_edit_visits_confirm_delete_keyboard(
    user_id: int,
    year: int,
    month: int,
    record_id: int,
) -> InlineKeyboardMarkup:
    """Generate keyboard with Delete and Cancel buttons for visit deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=EditVisitsCB(
                        action="confirm_delete",
                        user_id=user_id,
                        year=year,
                        month=month,
                        record_id=record_id,
                    ).pack(),
                    style="danger",
                ),
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=EditVisitsCB(
                        action="record",
                        user_id=user_id,
                        year=year,
                        month=month,
                        record_id=record_id,
                    ).pack(),
                    style="danger",
                ),
            ]
        ]
    )


def get_edit_visits_confirm_manual_keyboard(
    user_id: int,
) -> InlineKeyboardMarkup:
    """Generate keyboard with Add and Cancel buttons for manual visit addition."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Добавить",
                    callback_data=EditVisitsCB(
                        action="save_manual",
                        user_id=user_id,
                    ).pack(),
                    style="success",
                ),
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=EditVisitsCB(
                        action="user",
                        user_id=user_id,
                    ).pack(),
                    style="danger",
                ),
            ]
        ]
    )
