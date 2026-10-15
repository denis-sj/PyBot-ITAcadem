"""FSM states for attendance draft editing."""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class AttendanceEditSG(StatesGroup):
    """FSM states for editing attendance sheet import drafts."""

    waiting_for_row_selection_time = State()
    waiting_for_new_time = State()
    waiting_for_row_selection_user = State()
    waiting_for_user_selection = State()
    waiting_for_row_selection_exclude = State()


class EditVisitsSG(StatesGroup):
    """FSM states for /edit_visits administrator scenario."""

    waiting_for_user_number = State()
    waiting_for_edit_time = State()
    waiting_for_manual_date = State()
    waiting_for_manual_time = State()


_ATTENDANCE_FSM_STATES = frozenset(
    {
        AttendanceEditSG.waiting_for_row_selection_time.state,
        AttendanceEditSG.waiting_for_new_time.state,
        AttendanceEditSG.waiting_for_row_selection_user.state,
        AttendanceEditSG.waiting_for_user_selection.state,
        AttendanceEditSG.waiting_for_row_selection_exclude.state,
        EditVisitsSG.waiting_for_user_number.state,
        EditVisitsSG.waiting_for_edit_time.state,
        EditVisitsSG.waiting_for_manual_date.state,
        EditVisitsSG.waiting_for_manual_time.state,
    }
)


async def clear_attendance_fsm_state(state: FSMContext) -> bool:
    """Clear an in-progress attendance input flow and report whether one was active."""
    if await state.get_state() not in _ATTENDANCE_FSM_STATES:
        return False

    await state.clear()
    return True
