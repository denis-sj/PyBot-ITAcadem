"""Unit tests for attendance text formatters and UI rendering."""

from datetime import date, datetime, time

from pybot.dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecordReadDTO,
    AttendanceUserSummaryDTO,
)
from pybot.presentation.texts.attendance import (
    format_draft_preview,
    format_duration,
    format_import_notification,
    format_monthly_report,
    format_user_rank,
    format_user_summary,
    format_user_visits,
)


def test_format_duration() -> None:
    assert format_duration(0) == "0 мин"
    assert format_duration(-10) == "0 мин"
    assert format_duration(45) == "45 мин"
    assert format_duration(60) == "1 ч"
    assert format_duration(90) == "1 ч 30 мин"
    assert format_duration(125) == "2 ч 05 мин"


def test_format_draft_preview() -> None:
    item1 = AttendanceDraftItemDTO(
        item_id="i1",
        date=date(2026, 9, 25),
        raw_name="Иванов И.",
        matched_user_id=1,
        matched_user_name="Иванов Иван",
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        confidence=0.95,
        status="recognized",
    )
    item2 = AttendanceDraftItemDTO(
        item_id="i2",
        date=date(2026, 9, 25),
        raw_name="Неизвестный Н.",
        matched_user_id=None,
        matched_user_name=None,
        start_time=None,
        end_time=None,
        duration_minutes=0,
        is_empty_time=True,
        confidence=0.8,
        status="needs_manual_review",
    )
    ignored_block = AttendanceDraftDateBlockDTO(
        date=date(2026, 8, 30),
        is_ignored=True,
        ignore_reason="прошлый месяц",
        items=[],
    )
    valid_block = AttendanceDraftDateBlockDTO(
        date=date(2026, 9, 25),
        is_ignored=False,
        items=[item1, item2],
    )
    draft = AttendanceDraftDTO(
        draft_id=7,
        author_tg_id=123,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[valid_block, ignored_block],
        status="DRAFT",
    )

    rendered = format_draft_preview(draft)

    assert "черновик #7" in rendered
    assert "25.09.2026" in rendered
    assert "Иванов Иван" in rendered
    assert "18:00–20:00" in rendered
    assert "2 ч" in rendered
    assert "Неизвестный Н." in rendered
    assert "время не указано" in rendered
    assert "прошлый месяц" in rendered
    assert "Требует внимания: <b>1</b> ⚠️" in rendered


def test_format_draft_preview_all_dates_processed() -> None:
    block1 = AttendanceDraftDateBlockDTO(
        date=date(2026, 9, 20),
        is_ignored=True,
        ignore_reason="дата уже обработана",
        processed_import_id=42,
        items=[],
    )
    draft = AttendanceDraftDTO(
        draft_id=99,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[block1],
        status="DRAFT",
    )
    rendered = format_draft_preview(draft)
    assert "Все даты на фото уже учтены (импорт #42). Сохранять нечего." in rendered


def test_format_draft_preview_one_date_processed_one_active() -> None:
    block1 = AttendanceDraftDateBlockDTO(
        date=date(2026, 9, 20),
        is_ignored=True,
        ignore_reason="дата уже обработана",
        processed_import_id=15,
        items=[],
    )
    item = AttendanceDraftItemDTO(
        item_id="i1",
        date=date(2026, 9, 21),
        raw_name="Иванов И.",
        matched_user_id=1,
        matched_user_name="Иванов Иван",
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        confidence=0.95,
        status="recognized",
    )
    block2 = AttendanceDraftDateBlockDTO(
        date=date(2026, 9, 21),
        is_ignored=False,
        items=[item],
    )
    draft = AttendanceDraftDTO(
        draft_id=100,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[block1, block2],
        status="DRAFT",
    )
    rendered = format_draft_preview(draft)
    assert "20.09.2026 уже учтена (импорт #15). Строки за эту дату не будут сохранены." in rendered
    assert "21.09.2026" in rendered
    assert "Иванов Иван" in rendered


def test_format_draft_preview_past_month_warning() -> None:
    item = AttendanceDraftItemDTO(
        item_id="i1",
        date=date(2026, 8, 15),
        raw_name="Сидоров С.",
        matched_user_id=1,
        matched_user_name="Сидоров Сидор",
        start_time=time(18, 0),
        end_time=time(19, 0),
        duration_minutes=60,
        is_empty_time=False,
        confidence=0.95,
        status="recognized",
    )
    warning = (
        "Дата на фото: 15.08.2026 — это август 2026, не текущий месяц. "
        "Посещения попадут в отчёт за август 2026 и не изменят отчёт за сентябрь 2026."
    )
    block = AttendanceDraftDateBlockDTO(
        date=date(2026, 8, 15),
        is_ignored=False,
        period_warning=warning,
        items=[item],
    )
    draft = AttendanceDraftDTO(
        draft_id=101,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[block],
        status="DRAFT",
    )
    rendered = format_draft_preview(draft)
    assert warning in rendered
    assert "15.08.2026" in rendered


def test_format_user_summary() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Петров Петр",
        total_minutes=150,
        rank=2,
        total_participants=10,
        visits_count=2,
    )
    text = format_user_summary(summary, month=9, year=2026)

    assert "Петров Петр" in text
    assert "2 ч 30 мин" in text
    assert "#2" in text
    assert "из 10" in text
    assert "2" in text


def test_format_user_summary_no_visits() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Петров Петр",
        total_minutes=0,
        rank=0,
        total_participants=5,
        visits_count=0,
    )
    text = format_user_summary(summary, month=9, year=2026)

    assert "Петров Петр" in text
    assert "В этом месяце у вас пока нет посещений." in text
    assert "Место в рейтинге" not in text


def test_format_user_visits_empty() -> None:
    text = format_user_visits([], month=9, year=2026)
    assert "пока нет" in text


def test_format_user_visits_with_data() -> None:
    v1 = AttendanceRecordReadDTO(
        id=1,
        user_id=10,
        user_full_name="Петров Петр",
        date=date(2026, 9, 20),
        start_time=time(18, 0),
        end_time=time(19, 30),
        duration_minutes=90,
        is_empty_time=False,
        source="photo_ai",
        confirmed_at=datetime(2026, 9, 20, 20, 0),
    )
    text = format_user_visits([v1], month=9, year=2026)

    assert "20.09.2026" in text
    assert "18:00–19:30" in text
    assert "1 ч 30 мин" in text


def test_format_user_rank() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Сидоров С.",
        total_minutes=60,
        rank=5,
        total_participants=12,
        visits_count=1,
    )
    text = format_user_rank(summary, month=9, year=2026)
    assert "#5" in text
    assert "12" in text
    assert "1 ч" in text


def test_format_user_rank_no_visits() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Сидоров С.",
        total_minutes=0,
        rank=0,
        total_participants=5,
        visits_count=0,
    )
    text = format_user_rank(summary, month=9, year=2026)
    assert "В этом месяце у вас пока нет посещений." in text
    assert "Место:" not in text
    assert "Часов налета" not in text


def test_format_monthly_report_short_and_detailed() -> None:
    rank1 = AttendanceMonthlyRankingItemDTO(
        rank=1,
        user_id=1,
        user_full_name="Иванов И.",
        total_minutes=240,
        visits_count=2,
    )
    rank2 = AttendanceMonthlyRankingItemDTO(
        rank=2,
        user_id=2,
        user_full_name="Петров П.",
        total_minutes=120,
        visits_count=1,
    )
    rec1 = AttendanceRecordReadDTO(
        id=1,
        user_id=1,
        user_full_name="Иванов И.",
        date=date(2026, 9, 15),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        source="photo_ai",
        confirmed_at=datetime(2026, 9, 15, 20, 0),
    )
    report = AttendanceMonthlyReportDTO(
        year=2026,
        month=9,
        total_club_minutes=360,
        active_participants_count=2,
        average_minutes_per_participant=180,
        rankings=[rank1, rank2],
        records=[rec1],
    )

    short_text = format_monthly_report(report, detailed=False)
    assert "6 ч" in short_text
    assert "Иванов И." in short_text
    assert "🥇" in short_text
    assert "🥈" in short_text
    assert "Детализация" not in short_text

    detailed_text = format_monthly_report(report, detailed=True)
    assert "Детализация" in detailed_text
    assert "15.09" in detailed_text


def test_format_import_notification_single_visit() -> None:
    visits = [(date(2026, 9, 20), time(18, 0), time(20, 0), 120)]
    text = format_import_notification(import_id=123, user_visits=visits)
    assert "✅ <b>Добавлено посещение:</b>" in text
    assert "20.09.2026 — 18:00–20:00 (2 ч)" in text
    assert "Источник: импорт #123." in text
    assert "/my_visits" in text


def test_format_import_notification_multiple_visits_sorted() -> None:
    visits = [
        (date(2026, 9, 21), time(18, 0), time(19, 30), 90),
        (date(2026, 9, 20), time(18, 0), time(20, 0), 120),
    ]
    text = format_import_notification(import_id=123, user_visits=visits)
    assert "✅ <b>Добавлены посещения:</b>" in text
    lines = text.splitlines()
    assert "20.09.2026 — 18:00–20:00 (2 ч)" in lines[2]
    assert "21.09.2026 — 18:00–19:30 (1 ч 30 мин)" in lines[3]
    assert "Источник: импорт #123." in text
