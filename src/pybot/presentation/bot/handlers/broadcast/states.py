"""FSM states for the broadcast scenario started from the main menu."""

from aiogram.fsm.state import State, StatesGroup


class BroadcastMenuSG(StatesGroup):
    """States of the «Рассылка всем» scenario: text, confirmation, then the sending itself."""

    waiting_for_text = State()
    waiting_for_confirmation = State()
    sending = State()
