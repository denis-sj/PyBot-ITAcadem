"""Unit tests for attendance DTOs and helper functions."""

from datetime import date, datetime, time

import pytest

from pybot.dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceQualityDTO,
    AttendanceRecognitionResultDTO,
    AttendanceRecognizedDateBlockDTO,
    AttendanceRecognizedRowDTO,
    AttendanceRecordReadDTO,
    AttendanceUserSummaryDTO,
    calculate_duration_minutes,
    parse_time_range,
)


@pytest.mark.parametrize(
    ("raw_input", "expected_start", "expected_end", "expected_duration"),
    [
        ("18:00-20:00", time(18, 0), time(20, 0), 120),
        ("18.00-20.00", time(18, 0), time(20, 0), 120),
        ("18:00 - 20:00", time(18, 0), time(20, 0), 120),
        ("18:00—20:00", time(18, 0), time(20, 0), 120),
        ("18:00–20:00", time(18, 0), time(20, 0), 120),
        ("9:00 - 10:30", time(9, 0), time(10, 30), 90),
        ("09:15-09:45", time(9, 15), time(9, 45), 30),
        (" 14:00 - 16:15 ", time(14, 0), time(16, 15), 135),
    ],
)
def test_parse_time_range_valid_inputs(
    raw_input: str,
    expected_start: time,
    expected_end: time,
    expected_duration: int,
) -> None:
    result = parse_time_range(raw_input)
    assert result is not None
    start, end, duration = result
    assert start == expected_start
    assert end == expected_end
    assert duration == expected_duration


@pytest.mark.parametrize(
    "invalid_input",
    [
        "",
        "   ",
        "invalid",
        "20:00-18:00",  # end before start
        "18:00-18:00",  # duration 0
        "25:00-26:00",  # invalid hour
        "12:65-13:00",  # invalid minute
        "18:00",  # single time
        "18:00-",
        "-20:00",
        None,
    ],
)
def test_parse_time_range_invalid_inputs(invalid_input: str | None) -> None:
    assert parse_time_range(invalid_input) is None  # ty:ignore[invalid-argument-type]


def test_calculate_duration_minutes() -> None:
    assert calculate_duration_minutes(time(18, 0), time(19, 30)) == 90
    assert calculate_duration_minutes(time(10, 15), time(12, 45)) == 150
    assert calculate_duration_minutes(time(18, 0), time(18, 0)) == 0
    assert calculate_duration_minutes(time(19, 0), time(18, 0)) == 0


def test_dto_immutability() -> None:
    quality = AttendanceQualityDTO(is_good=True, comment="Четкое фото", problems=[])
    with pytest.raises(Exception):
        setattr(quality, "is_good", False)

    row = AttendanceRecognizedRowDTO(raw_name="Иванов И.", time_raw="18:00-19:00", duration_minutes=60)
    with pytest.raises(Exception):
        setattr(row, "duration_minutes", 90)

    # Verify model_copy works per ADR 002
    updated_row = row.model_copy(update={"duration_minutes": 90})
    assert updated_row.duration_minutes == 90
    assert row.duration_minutes == 60


def test_attendance_draft_and_report_dto_creation() -> None:
    item = AttendanceDraftItemDTO(
        item_id="item-1",
        date=date(2026, 9, 25),
        raw_name="Петров П.",
        matched_user_id=10,
        matched_user_name="Петров Петр",
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        confidence=0.98,
        status="recognized",
    )
    block = AttendanceDraftDateBlockDTO(
        date=date(2026, 9, 25),
        is_ignored=False,
        items=[item],
    )
    draft = AttendanceDraftDTO(
        draft_id=1,
        author_tg_id=123456,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=10240,
        photo_checksum="abc123sha",
        dates=[block],
        status="DRAFT",
    )
    assert draft.draft_id == 1
    assert len(draft.dates) == 1
    assert draft.dates[0].items[0].matched_user_id == 10

    ranking_item = AttendanceMonthlyRankingItemDTO(
        rank=1,
        user_id=10,
        user_full_name="Петров Петр",
        total_minutes=120,
        visits_count=1,
    )
    report = AttendanceMonthlyReportDTO(
        year=2026,
        month=9,
        total_club_minutes=120,
        active_participants_count=1,
        average_minutes_per_participant=120,
        rankings=[ranking_item],
    )
    assert report.total_club_minutes == 120
    assert len(report.rankings) == 1
