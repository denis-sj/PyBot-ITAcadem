"""Unit tests for attendance domain exceptions."""

from pybot.domain.exceptions import (
    AttendanceDateAlreadyProcessedError,
    AttendanceDateOutOfRangeError,
    AttendanceDomainError,
    AttendanceDraftExpiredError,
    AttendanceDraftNotFoundError,
    AttendanceNetworkError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
    AttendanceUnauthorizedError,
    AttendanceUserNotFoundError,
    DomainError,
)


def test_attendance_exceptions_inherit_from_domain_error() -> None:
    exceptions = [
        AttendanceDomainError(),
        AttendanceRecognitionError(),
        AttendanceNetworkError(),
        AttendanceDraftNotFoundError(import_id=123),
        AttendanceDraftExpiredError(import_id=123),
        AttendanceDateAlreadyProcessedError(processed_date="2026-09-25"),
        AttendanceDateOutOfRangeError(target_date="2026-10-01"),
        AttendancePhotoEmptyError(),
        AttendanceUserNotFoundError(raw_name="Иванов И."),
        AttendanceUnauthorizedError(user_tg_id=999),
    ]

    for exc in exceptions:
        assert isinstance(exc, AttendanceDomainError)
        assert isinstance(exc, DomainError)
        assert str(exc).startswith(f"[{exc.code}]")


def test_attendance_draft_not_found_details() -> None:
    exc = AttendanceDraftNotFoundError(import_id=42)
    assert exc.details["import_id"] == 42
    assert exc.code == "ATTENDANCE_DRAFT_NOT_FOUND"


def test_attendance_date_already_processed_details() -> None:
    exc = AttendanceDateAlreadyProcessedError(processed_date="2026-09-20")
    assert exc.details["processed_date"] == "2026-09-20"
    assert exc.code == "ATTENDANCE_DATE_ALREADY_PROCESSED"


def test_attendance_user_not_found_details() -> None:
    exc = AttendanceUserNotFoundError(raw_name="Неизвестный Участник")
    assert exc.details["raw_name"] == "Неизвестный Участник"
    assert exc.code == "ATTENDANCE_USER_NOT_FOUND"
