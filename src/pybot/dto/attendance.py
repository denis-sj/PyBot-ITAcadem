"""DTOs and value objects for photo attendance accounting."""

import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import Field

from .base_dto import BaseDTO

_HOURS_PER_DAY = 24
_MINUTES_PER_HOUR = 60
_TIME_RANGE_PATTERN = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*[-—–~]\s*(\d{1,2})[:.](\d{2})\s*$")


def calculate_duration_minutes(start_time: time, end_time: time) -> int:
    """Calculate duration in minutes between start_time and end_time.

    Args:
        start_time: Beginning of the interval.
        end_time: End of the interval.

    Returns:
        Duration in minutes, or 0 if end_time <= start_time.
    """
    start_minutes = start_time.hour * _MINUTES_PER_HOUR + start_time.minute
    end_minutes = end_time.hour * _MINUTES_PER_HOUR + end_time.minute
    return max(0, end_minutes - start_minutes)


def parse_time_range(raw: str) -> tuple[time, time, int] | None:
    """Normalize and parse a time range string into (start_time, end_time, duration_minutes).

    Normalizes separators (dots/dashes/en-dash/em-dash), parses HH:MM-HH:MM,
    validates 0 <= H < 24 and 0 <= M < 60, and calculates duration.

    Args:
        raw: Raw time range string (e.g. '18:00-19:30', '18.00 - 19.30').

    Returns:
        Tuple of (start_time, end_time, duration_minutes), or None if invalid or duration <= 0.
    """
    if not raw or not isinstance(raw, str):
        return None

    match = _TIME_RANGE_PATTERN.match(raw.strip())
    if not match:
        return None

    try:
        start_h, start_m = int(match.group(1)), int(match.group(2))
        end_h, end_m = int(match.group(3)), int(match.group(4))
    except (ValueError, TypeError):  # fmt: skip
        return None

    valid_start = 0 <= start_h < _HOURS_PER_DAY and 0 <= start_m < _MINUTES_PER_HOUR
    valid_end = 0 <= end_h < _HOURS_PER_DAY and 0 <= end_m < _MINUTES_PER_HOUR
    if not (valid_start and valid_end):
        return None

    start_total = start_h * _MINUTES_PER_HOUR + start_m
    end_total = end_h * _MINUTES_PER_HOUR + end_m
    duration = end_total - start_total

    if duration <= 0:
        return None

    return time(start_h, start_m), time(end_h, end_m), duration


# ============ RECOGNITION DTOS ============


class AttendanceQualityDTO(BaseDTO):
    """Assessment of the attendance sheet photo quality."""

    is_good: bool
    comment: str
    problems: list[str] = Field(default_factory=list)


class AttendanceRecognizedRowDTO(BaseDTO):
    """Single participant row recognized from an attendance sheet."""

    raw_name: str | None = None
    time_raw: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    duration_minutes: int = 0
    is_empty_time: bool = False
    confidence: float = 1.0
    status: Literal["recognized", "needs_manual_review"] = "recognized"


class AttendanceRecognizedDateBlockDTO(BaseDTO):
    """Block of recognized rows belonging to a single calendar date."""

    date: date
    rows: list[AttendanceRecognizedRowDTO]


class AttendanceRecognitionResultDTO(BaseDTO):
    """Full result of AI attendance sheet recognition."""

    quality: AttendanceQualityDTO
    dates: list[AttendanceRecognizedDateBlockDTO]


# ============ DRAFT DTOS ============


class AttendanceDraftItemDTO(BaseDTO):
    """Editable participant attendance row inside an import draft."""

    item_id: str
    date: date
    raw_name: str | None
    matched_user_id: int | None
    matched_user_name: str | None
    start_time: time | None
    end_time: time | None
    duration_minutes: int
    is_empty_time: bool
    confidence: float
    status: Literal["recognized", "needs_manual_review"]
    is_duplicate_date: bool = False
    is_out_of_month: bool = False
    is_excluded: bool = False


class AttendanceDraftDateBlockDTO(BaseDTO):
    """Date group containing draft items with optional exclusion/ignore flag."""

    date: date
    is_ignored: bool = False
    ignore_reason: str | None = None
    processed_import_id: int | None = None
    period_warning: str | None = None
    items: list[AttendanceDraftItemDTO]


class AttendanceDraftDTO(BaseDTO):
    """Complete attendance import draft state."""

    draft_id: int
    author_tg_id: int
    created_at: datetime
    photo_mime_type: str | None
    photo_file_size: int | None
    photo_checksum: str | None
    dates: list[AttendanceDraftDateBlockDTO]
    status: str


# ============ QUERY & REPORT DTOS ============


class AttendanceRecordReadDTO(BaseDTO):
    """Confirmed attendance record read model."""

    id: int
    user_id: int
    user_full_name: str
    date: date
    start_time: time | None
    end_time: time | None
    duration_minutes: int
    is_empty_time: bool
    source: str
    confirmed_at: datetime


class AttendanceUserSummaryDTO(BaseDTO):
    """Summary of user's attendance metrics for a specific month."""

    user_id: int
    user_full_name: str
    total_minutes: int
    rank: int
    total_participants: int
    visits_count: int


class AttendanceMonthlyRankingItemDTO(BaseDTO):
    """Single item in monthly participant leaderboard rankings."""

    rank: int
    user_id: int
    user_full_name: str
    total_minutes: int
    visits_count: int


class AttendanceMonthlyReportDTO(BaseDTO):
    """Aggregated club monthly attendance report."""

    year: int
    month: int
    total_club_minutes: int
    active_participants_count: int
    average_minutes_per_participant: int
    rankings: list[AttendanceMonthlyRankingItemDTO]
    records: list[AttendanceRecordReadDTO] = Field(default_factory=list)
