"""FSM states for attendance draft editing."""

from aiogram.fsm.state import State, StatesGroup


class AttendanceEditSG(StatesGroup):
    """FSM states for editing attendance sheet import drafts."""

    waiting_for_row_selection_time = State()
    waiting_for_new_time = State()
    waiting_for_row_selection_user = State()
    waiting_for_user_selection = State()
    waiting_for_row_selection_exclude = State()
