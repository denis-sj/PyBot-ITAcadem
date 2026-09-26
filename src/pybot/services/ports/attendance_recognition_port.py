"""Port for attendance sheet photo recognition."""

from typing import Protocol

from ...dto import AttendanceRecognitionResultDTO


class AttendanceRecognitionPort(Protocol):
    """Contract for external vision recognition of attendance sheets."""

    async def recognize_attendance_sheet(
        self,
        photo_bytes: bytes,
        mime_type: str,
        candidate_user_names: list[str],
    ) -> AttendanceRecognitionResultDTO:
        """Recognize attendance sheet photo and extract structured attendance data.

        Args:
            photo_bytes: Raw image file bytes.
            mime_type: Image MIME type (e.g. 'image/jpeg', 'image/png').
            candidate_user_names: List of known club member full names to guide matching.

        Returns:
            AttendanceRecognitionResultDTO: Quality assessment and extracted dates/rows.

        Raises:
            AttendanceRecognitionError: If recognition failed or output could not be parsed.
            AttendanceNetworkError: On connection failure, timeout, or HTTP error.
        """
        ...
