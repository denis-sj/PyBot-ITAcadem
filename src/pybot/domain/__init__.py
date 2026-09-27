"""Domain package for PyBot."""

from .exceptions import (
    AttendancePhotoAccessDeniedError as AttendancePhotoAccessDeniedError,
    AttendancePhotoNotFoundError as AttendancePhotoNotFoundError,
    InsufficientPointsBalanceError as InsufficientPointsBalanceError,
    RoleRequestNotAllowedError as RoleRequestNotAllowedError,
)

__all__ = [
    "AttendancePhotoAccessDeniedError",
    "AttendancePhotoNotFoundError",
    "InsufficientPointsBalanceError",
    "RoleRequestNotAllowedError",
]
