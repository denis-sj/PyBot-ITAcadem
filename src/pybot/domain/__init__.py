"""Domain package for PyBot."""

from .exceptions import (
    AttendancePhotoAccessDeniedError as AttendancePhotoAccessDeniedError,
    AttendancePhotoNotFoundError as AttendancePhotoNotFoundError,
)

__all__ = [
    "AttendancePhotoAccessDeniedError",
    "AttendancePhotoNotFoundError",
]
