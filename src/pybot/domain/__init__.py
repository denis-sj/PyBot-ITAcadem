"""Domain package for PyBot."""

from .exceptions import (
    AttendancePhotoAccessDeniedError as AttendancePhotoAccessDeniedError,
    AttendancePhotoNotFoundError as AttendancePhotoNotFoundError,
    InsufficientPointsBalanceError as InsufficientPointsBalanceError,
)

__all__ = [
    "AttendancePhotoAccessDeniedError",
    "AttendancePhotoNotFoundError",
    "InsufficientPointsBalanceError",
]
