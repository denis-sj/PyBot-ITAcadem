"""Keyboards and callbacks package."""

from .navigation import (
    CommonNavigationCB as CommonNavigationCB,
    get_close_button as get_close_button,
    get_close_keyboard as get_close_keyboard,
)

__all__ = [
    "CommonNavigationCB",
    "get_close_button",
    "get_close_keyboard",
]
