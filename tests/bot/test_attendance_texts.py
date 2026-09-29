"""Unit tests for attendance text formatters and UI rendering."""

from datetime import date, datetime, time

from pybot.dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecordReadDTO,
    AttendanceTopDTO,
    AttendanceUserSummaryDTO,
)
from pybot.presentation.texts.attendance import (
    format_draft_preview,
    format_duration,
    format_edit_visits_dates_screen,
    format_edit_visits_record_card,
    format_edit_visits_user_screen,
    format_edit_visits_users_list,
    format_import_notification,
    format_monthly_report,
    format_my_visits_month_report,
    format_last_first_name,
    format_month_top,
    format_period_range,
    format_user_full_name,
    format_user_short_name,
    format_visits_home,
    format_week_top,
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
        status="recognized",
        is_excluded=True,
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
    assert "Неизвестный Н.»</i>: N" in rendered
    assert "время не указано" not in rendered
    assert "прошлый месяц" in rendered
    assert "Требует внимания" not in rendered


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
    assert "Все даты на фото уже учтены (Номер фото #42). Сохранять нечего." in rendered


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
    assert "20.09.2026 уже учтена (Номер фото #15). Строки за эту дату не будут сохранены." in rendered
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


def test_format_draft_preview_with_excluded_item_and_reason() -> None:
    item = AttendanceDraftItemDTO(
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
        is_excluded=True,
        exclusion_reason="У участника Иванов Иван уже есть посещение за 25.09.2026 (добавлено вручную).",
    )
    block = AttendanceDraftDateBlockDTO(date=date(2026, 9, 25), items=[item])
    draft = AttendanceDraftDTO(
        draft_id=12,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[block],
        status="DRAFT",
    )
    rendered = format_draft_preview(draft)

    assert "🚫" in rendered
    assert "[исключено]" in rendered
    assert "У участника Иванов Иван уже есть посещение за 25.09.2026 (добавлено вручную)." in rendered


def _ranking_item(rank: int, user_id: int, name: str, minutes: int, visits: int) -> AttendanceMonthlyRankingItemDTO:
    return AttendanceMonthlyRankingItemDTO(
        rank=rank,
        user_id=user_id,
        user_full_name=name,
        total_minutes=minutes,
        visits_count=visits,
    )


def _top(
    rows: list[AttendanceMonthlyRankingItemDTO],
    total: int | None = None,
    current_user: AttendanceMonthlyRankingItemDTO | None = None,
    period: tuple[date, date] = (date(2026, 9, 28), date(2026, 10, 3)),
) -> AttendanceTopDTO:
    return AttendanceTopDTO(
        period_start=period[0],
        period_end=period[1],
        rows=rows,
        total_participants=len(rows) if total is None else total,
        current_user=current_user,
    )


def test_format_visits_home_shows_summary_with_last_and_first_name() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Иванов Иван Иванович",
        total_minutes=750,
        rank=3,
        total_participants=18,
        visits_count=6,
    )
    text = format_visits_home(summary, month=9, year=2026)

    assert "📊 <b>Посещаемость · сентябрь 2026</b>" in text
    assert "👤 Иванов Иван\n" in text
    assert "Иванович" not in text
    assert "⏱ Время: <b>12 ч 30 мин</b>" in text
    assert "🗓 Посещений: <b>6</b>" in text
    assert "🏆 Место в рейтинге месяца: <b>3</b> из 18" in text
    assert "администратор" not in text


def test_format_visits_home_without_visits_names_month() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=10,
        user_full_name="Петров Петр",
        total_minutes=0,
        rank=0,
        total_participants=5,
        visits_count=0,
    )
    text = format_visits_home(summary, month=10, year=2026)

    assert "Петров Петр" in text
    assert "В октябре у вас пока нет подтверждённых посещений." in text
    assert "Место в рейтинге" not in text


def test_format_last_first_name_handles_missing_parts() -> None:
    assert format_last_first_name("Иванов Иван Иванович") == "Иванов Иван"
    assert format_last_first_name("Иванов Иван") == "Иванов Иван"
    assert format_last_first_name("Иванов") == "Иванов"


def test_format_week_top_lists_participants_with_hours_and_visits() -> None:
    rows = [
        _ranking_item(1, 1, "Смирнова Анна Ивановна", 570, 4),
        _ranking_item(2, 2, "Иванов Иван", 480, 3),
        _ranking_item(2, 3, "Ким Олег", 480, 4),
        _ranking_item(4, 4, "Лис Мария", 375, 2),
        _ranking_item(5, 5, "Орлов Павел", 300, 1),
    ]
    text = format_week_top(_top(rows), viewer_user_id=99)

    assert "🏆 <b>Топ недели · 28 сентября – 3 октября</b>" in text
    assert "1. Смирнова Анна — 9 ч 30 мин · 4 посещения" in text
    assert "2. Иванов Иван — 8 ч · 3 посещения" in text
    assert "2. Ким Олег — 8 ч · 4 посещения" in text
    assert "4. Лис Мария — 6 ч 15 мин · 2 посещения" in text
    assert "5. Орлов Павел — 5 ч · 1 посещение" in text
    assert "В рейтинге пока" not in text
    assert "Вас пока нет в рейтинге этого периода." in text


def test_format_week_top_shows_all_tied_for_last_place() -> None:
    rows = [
        _ranking_item(1, 1, "А Один", 600, 1),
        _ranking_item(2, 2, "Б Два", 500, 1),
        _ranking_item(2, 3, "В Три", 500, 1),
        _ranking_item(4, 4, "Г Четыре", 400, 1),
        _ranking_item(5, 5, "Д Пять", 300, 1),
        _ranking_item(5, 6, "Е Шесть", 300, 1),
        _ranking_item(5, 7, "Ж Семь", 300, 1),
    ]
    text = format_week_top(_top(rows, total=9), viewer_user_id=99)

    assert "5. Д Пять" in text
    assert "5. Е Шесть" in text
    assert "5. Ж Семь" in text
    assert "…и ещё" not in text


def test_format_week_top_collapses_more_than_ten_rows() -> None:
    rows = [_ranking_item(1, 1, "Первый Лидер", 900, 3)]
    rows += [_ranking_item(2, uid, f"Участник{uid}", 600, 2) for uid in range(2, 14)]
    text = format_week_top(_top(rows, total=13), viewer_user_id=99)

    assert text.count("\n2. ") == 9
    assert "…и ещё 3 участника на 2-м месте" in text


def test_format_week_top_shows_viewer_place_when_hidden_by_tie_overflow() -> None:
    rows = [_ranking_item(1, 1, "Первый Лидер", 900, 3)]
    rows += [_ranking_item(2, uid, f"Участник{uid}", 600, 2) for uid in range(2, 14)]
    hidden_viewer = rows[-1]
    text = format_week_top(_top(rows, total=13, current_user=hidden_viewer), viewer_user_id=13)

    assert "Участник13" not in text
    assert "…и ещё 3 участника на 2-м месте" in text
    assert "Вы: 2-е место из 13 · 10 ч · 2 посещения" in text


def test_format_week_top_fewer_than_five_participants() -> None:
    rows = [_ranking_item(1, 1, "Смирнова Анна", 120, 1), _ranking_item(2, 2, "Иванов Иван", 60, 1)]
    text = format_week_top(_top(rows), viewer_user_id=99)

    assert "В рейтинге пока 2 участника" in text
    assert "3." not in text


def test_format_week_top_highlights_viewer_inside_top_without_extra_line() -> None:
    me = _ranking_item(1, 1, "Смирнова Анна", 120, 1)
    text = format_week_top(_top([me], current_user=me), viewer_user_id=1)

    assert "<b>1. Смирнова Анна — 2 ч · 1 посещение</b>" in text
    assert "Вы:" not in text
    assert "Вас пока нет" not in text


def test_format_week_top_shows_viewer_place_outside_top() -> None:
    rows = [_ranking_item(place, place, f"Участник{place}", 700 - place * 10, 1) for place in range(1, 6)]
    me = _ranking_item(11, 99, "Я Сам", 195, 1)
    text = format_week_top(_top(rows, total=18, current_user=me), viewer_user_id=99)

    assert "Вы: 11-е место из 18 · 3 ч 15 мин · 1 посещение" in text


def test_format_week_top_empty_period() -> None:
    text = format_week_top(_top([]), viewer_user_id=99)

    assert "За эту неделю пока нет подтверждённых посещений." in text
    assert "Вас пока нет" not in text


def test_format_month_top_title_and_empty_month() -> None:
    period = (date(2026, 10, 1), date(2026, 10, 31))
    text = format_month_top(_top([], period=period), viewer_user_id=99)

    assert "🏆 <b>Топ месяца · октябрь 2026</b>" in text
    assert "В октябре пока нет подтверждённых посещений." in text


def test_format_month_top_escapes_names() -> None:
    period = (date(2026, 9, 1), date(2026, 9, 30))
    rows = [_ranking_item(1, 1, "<b>Хакер</b> Иван", 60, 1)]
    text = format_month_top(_top(rows, period=period), viewer_user_id=99)

    assert "&lt;b&gt;Хакер&lt;/b&gt; Иван" in text


def test_format_period_range() -> None:
    assert format_period_range(date(2026, 9, 21), date(2026, 9, 26)) == "21–26 сентября"
    assert format_period_range(date(2026, 9, 28), date(2026, 10, 3)) == "28 сентября – 3 октября"
    assert format_period_range(date(2025, 12, 29), date(2026, 1, 3)) == "29 декабря – 3 января"


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
    assert "Номер фото #123." in text
    assert "/visits" in text
    assert "/my_visits" not in text


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


def test_format_my_visits_month_report() -> None:
    summary = AttendanceUserSummaryDTO(
        user_id=1,
        user_full_name="Иванов И.",
        total_minutes=461,
        rank=3,
        total_participants=12,
        visits_count=2,
    )
    v1 = AttendanceRecordReadDTO(
        id=1,
        import_id=123,
        user_id=1,
        user_full_name="Иванов И.",
        date=date(2026, 6, 23),
        start_time=time(12, 25),
        end_time=time(17, 5),
        duration_minutes=280,
        is_empty_time=False,
        source="photo_ai",
        is_time_modified=False,
        confirmed_at=datetime(2026, 6, 23, 20, 0),
    )
    v2 = AttendanceRecordReadDTO(
        id=2,
        import_id=123,
        user_id=1,
        user_full_name="Иванов И.",
        date=date(2026, 6, 24),
        start_time=time(14, 25),
        end_time=time(16, 30),
        duration_minutes=125,
        is_empty_time=False,
        source="photo_ai",
        is_time_modified=True,
        confirmed_at=datetime(2026, 6, 24, 20, 0),
    )
    v3 = AttendanceRecordReadDTO(
        id=3,
        import_id=None,
        user_id=1,
        user_full_name="Иванов И.",
        date=date(2026, 6, 25),
        start_time=time(10, 0),
        end_time=time(11, 0),
        duration_minutes=60,
        is_empty_time=False,
        source="manual",
        is_time_modified=False,
        confirmed_at=datetime(2026, 6, 25, 20, 0),
    )
    text = format_my_visits_month_report(year=2026, month=6, summary=summary, visits=[v2, v1, v3])

    assert "📅 <b>Июнь 2026</b>" in text
    assert "Посещений: 2 · Всего: 7 ч 41 мин · Место: 3 из 12" in text
    assert "23.06 — 12:25–17:05 (4 ч 40 мин) · Номер фото #123" in text
    assert "24.06 — 14:25–16:30 (2 ч 05 мин) · Номер фото #123 · время изменено администратором" in text
    assert "25.06 — 10:00–11:00 (1 ч) · добавлено вручную" in text


def test_format_user_name_helpers() -> None:
    class DummyUser1:
        last_name = "Иванов"
        first_name = "Иван"
        patronymic = "Иванович"

    u1 = DummyUser1()
    assert format_user_full_name(u1) == "Иванов Иван Иванович"
    assert format_user_short_name(u1) == "Иванов И. И."

    class DummyUser2:
        last_name = "Петров"
        first_name = "Петр"
        patronymic = None

    u2 = DummyUser2()
    assert format_user_full_name(u2) == "Петров Петр"
    assert format_user_short_name(u2) == "Петров П."


def test_format_edit_visits_users_list() -> None:
    class DummyUser:
        def __init__(self, last: str, first: str, patr: str | None = None) -> None:
            self.last_name = last
            self.first_name = first
            self.patronymic = patr

    users = [DummyUser("Иванов", "Иван", "Иванович"), DummyUser("Петров", "Петр")]
    rendered = format_edit_visits_users_list(users, page=0, page_size=40)
    assert "<blockquote>" in rendered
    assert "<blockquote expandable>" not in rendered
    assert "1. Иванов Иван Иванович" in rendered
    assert "2. Петров Петр" in rendered
    assert "Введите номер участника." in rendered


def test_format_edit_visits_user_screen() -> None:
    class DummyUser:
        last_name = "Иванов"
        first_name = "Иван"
        patronymic = "Иванович"

    user = DummyUser()
    with_visits = format_edit_visits_user_screen(user, has_visits=True)
    assert "👤 <b>Иванов Иван Иванович</b>" in with_visits
    assert "Выберите месяц или добавьте посещение:" in with_visits

    no_visits = format_edit_visits_user_screen(user, has_visits=False)
    assert "👤 <b>Иванов Иван Иванович</b>" in no_visits
    assert "У участника пока нет посещений." in no_visits


def test_format_edit_visits_dates_screen() -> None:
    class DummyUser:
        last_name = "Иванов"
        first_name = "Иван"
        patronymic = "Иванович"

    rendered = format_edit_visits_dates_screen(DummyUser(), year=2026, month=9)
    assert "👤 <b>Иванов И. И. · Сентябрь 2026</b>" in rendered
    assert "Выберите дату посещения:" in rendered


def test_format_edit_visits_record_card() -> None:
    class DummyUser:
        last_name = "Иванов"
        first_name = "Иван"
        patronymic = "Иванович"

    class DummyRecord:
        date = date(2026, 9, 25)
        start_time = time(18, 0)
        end_time = time(20, 0)
        duration_minutes = 120
        import_id = 42

    rendered = format_edit_visits_record_card(DummyUser(), DummyRecord())
    assert "👤 <b>Иванов И. И. · 25.09.2026</b>" in rendered
    assert "Время: <b>18:00–20:00</b> (2 ч)" in rendered
    assert "Источник: Номер фото #42" in rendered


def test_format_draft_preview_keeps_incomplete_handwritten_time_visible() -> None:
    item = AttendanceDraftItemDTO(
        item_id="i1",
        date=date(2026, 6, 16),
        raw_name="Участник Тестовый",
        matched_user_id=1,
        matched_user_name="Участник Тестовый",
        start_time=None,
        end_time=None,
        duration_minutes=0,
        is_empty_time=False,
        confidence=0.95,
        status="needs_manual_review",
        time_raw="8:40-",
    )
    draft = AttendanceDraftDTO(
        draft_id=7,
        author_tg_id=1,
        created_at=datetime(2026, 6, 16, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[AttendanceDraftDateBlockDTO(date=date(2026, 6, 16), items=[item])],
        status="DRAFT",
    )

    rendered = format_draft_preview(draft)

    assert "⚠️ Участник Тестовый: на листе «8:40-» (требует ввода времени)" in rendered
    assert "Требует внимания: <b>1</b>" in rendered
