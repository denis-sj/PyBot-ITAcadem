"""Unit tests for AttendanceService orchestration and use cases."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from dishka import make_async_container
from pydantic import SecretStr

from pybot.core.config import AppSettings
from pybot.db.models import (
    AttendanceChangeHistory,
    AttendanceDate,
    AttendanceImport,
    AttendanceImportStatus,
    AttendanceRecord,
    User,
)
from pybot.di import containers as di_containers
from pybot.domain.exceptions import (
    AttendanceDomainError,
    AttendanceDraftExpiredError,
    AttendancePhotoAccessDeniedError,
    AttendancePhotoEmptyError,
    AttendancePhotoNotFoundError,
    AttendanceRecognitionError,
    AttendanceUserNotFoundError,
)
from pybot.dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceQualityDTO,
    AttendanceRecognitionResultDTO,
    AttendanceRecognizedDateBlockDTO,
    AttendanceRecognizedRowDTO,
)
from pybot.infrastructure import AttendanceRepository, UserRepository
from pybot.infrastructure.adapters import PolzaVisionAdapter
from pybot.services.attendance_service import AttendanceService
from pybot.services.ports import AttendanceRecognitionPort, NotificationPort


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_attendance_repo() -> AsyncMock:
    repo = AsyncMock(spec=AttendanceRepository)
    repo.is_date_processed.return_value = False
    repo.find_processed_date.return_value = None
    repo.find_user_record_on_date.return_value = None
    repo.has_user_visit_on_date.return_value = False
    repo.find_modified_record_ids.return_value = set()

    async def _mock_save(
        db: Any, import_id: int, confirmed_by_tg_id: int, confirmed_dates: list[tuple[date, list[dict[str, Any]]]]
    ) -> list[tuple[date, list[dict[str, Any]]]]:
        return confirmed_dates

    repo.save_confirmed_import.side_effect = _mock_save
    return repo


@pytest.fixture
def mock_user_repo() -> AsyncMock:
    repo = AsyncMock(spec=UserRepository)
    return repo


@pytest.fixture
def mock_recognition_port() -> AsyncMock:
    port = AsyncMock(spec=AttendanceRecognitionPort)
    return port


@pytest.fixture
def mock_notification_service() -> AsyncMock:
    return AsyncMock(spec=NotificationPort)


@pytest.fixture
def service(
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    mock_notification_service: AsyncMock,
    settings_obj: AppSettings,
) -> AttendanceService:
    settings_obj.attendance_timezone = "Asia/Yekaterinburg"
    return AttendanceService(
        db=mock_db,
        attendance_repo=mock_attendance_repo,
        user_repo=mock_user_repo,
        recognition_port=mock_recognition_port,
        settings=settings_obj,
        notification_service=mock_notification_service,
    )


@pytest.mark.asyncio
async def test_sorted_participants_uses_all_registered_users_without_requiring_visits(
    service: AttendanceService,
    mock_user_repo: AsyncMock,
) -> None:
    registered_user_without_visits = SimpleNamespace(
        id=771,
        last_name="Петров",
        first_name="Пётр",
        patronymic=None,
        telegram_id=771234,
        telegram_username="petya",
    )
    mock_user_repo.find_all_users.return_value = [registered_user_without_visits]

    participants = await service.get_sorted_participants()

    assert participants == [registered_user_without_visits]
    mock_user_repo.find_all_users.assert_awaited_once()


@pytest.mark.asyncio
async def test_photo_browser_service_uses_month_date_range(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.find_import_photo_months.return_value = [(2026, 9)]
    mock_attendance_repo.find_import_photo_dates_for_month.return_value = [(date(2026, 9, 25), 42)]

    months = await service.get_import_photo_months()
    dates = await service.get_import_photo_dates_for_month(year=2026, month=9)

    assert months == [(2026, 9)]
    assert dates == [(date(2026, 9, 25), 42)]
    mock_attendance_repo.find_import_photo_dates_for_month.assert_awaited_once()
    args = mock_attendance_repo.find_import_photo_dates_for_month.await_args
    assert args is not None
    assert args.args[1:] == (date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.asyncio
async def test_photo_browser_service_rejects_invalid_month_without_query(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    assert await service.get_import_photo_dates_for_month(year=2026, month=13) == []
    mock_attendance_repo.find_import_photo_dates_for_month.assert_not_awaited()


# ============ PHOTO INGESTION TESTS ============


@pytest.mark.asyncio
async def test_ingest_attendance_photo_empty_raises(service: AttendanceService) -> None:
    with pytest.raises(AttendancePhotoEmptyError, match="Файл фотографии пуст"):
        await service.ingest_attendance_photo(
            author_tg_id=123,
            photo_bytes=b"",
            mime_type="image/jpeg",
        )


@pytest.mark.asyncio
async def test_ingest_attendance_photo_bad_quality_raises(
    service: AttendanceService,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
) -> None:
    mock_user_repo.find_all_users.return_value = []
    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(
            is_good=False,
            comment="Изображение слишком размыто",
            problems=["смазанные символы"],
        ),
        dates=[],
    )

    with pytest.raises(AttendanceRecognitionError, match="Изображение слишком размыто"):
        await service.ingest_attendance_photo(
            author_tg_id=123,
            photo_bytes=b"some_bytes",
            mime_type="image/jpeg",
        )


@pytest.mark.asyncio
async def test_ingest_attendance_photo_happy_path(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> None:
    tz = ZoneInfo(str(settings_obj.attendance_timezone))
    now = datetime.now(tz)
    today = now.date()

    user1 = User(id=1, first_name="Иван", last_name="Иванов", patronymic="Иванович")
    user2 = User(id=2, first_name="Петр", last_name="Петров")
    mock_user_repo.find_all_users.return_value = [user1, user2]

    # AI result with 2 rows: one with valid time, one with empty time
    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="Четкий снимок", problems=[]),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=today,
                rows=[
                    AttendanceRecognizedRowDTO(
                        raw_name="Иванов Иван",
                        time_raw="18:00-20:00",
                        duration_minutes=999,  # Should be recomputed to 120
                        is_empty_time=False,
                        confidence=0.95,
                    ),
                    AttendanceRecognizedRowDTO(
                        raw_name="Петров Петр",
                        time_raw="-",
                        is_empty_time=True,
                        confidence=0.90,
                    ),
                ],
            )
        ],
    )

    mock_draft_record = AttendanceImport(id=42, status=AttendanceImportStatus.DRAFT.value)
    mock_attendance_repo.create_draft.return_value = mock_draft_record

    draft = await service.ingest_attendance_photo(
        author_tg_id=777,
        photo_bytes=b"valid_photo_bytes",
        mime_type="image/jpeg",
    )

    assert draft.draft_id == 42
    assert draft.author_tg_id == 777
    assert len(draft.dates) == 1

    date_block = draft.dates[0]
    assert date_block.date == today
    assert date_block.is_ignored is False
    assert len(date_block.items) == 2

    # Check item 1: Иванов Иван, 120 min duration, recognized
    item1 = date_block.items[0]
    assert item1.matched_user_id == 1
    assert item1.duration_minutes == 120
    assert item1.status == "recognized"
    assert item1.is_empty_time is False

    # Absence rows stay visible in the draft as N markers but cannot be confirmed as visits.
    item2 = date_block.items[1]
    assert item2.raw_name == "Петров Петр"
    assert item2.is_empty_time is True
    assert item2.duration_minutes == 0
    assert item2.start_time is None
    assert item2.end_time is None
    assert item2.is_excluded is True
    # Verify stale drafts expired before draft creation
    mock_attendance_repo.expire_stale_drafts.assert_awaited_once()
    # Transactions committed (stale drafts expired + draft created)
    assert mock_db.commit.await_count == 2


# ============ PARTICIPANT MATCHING TESTS ============


@pytest.mark.asyncio
async def test_participant_matching_ambiguous_and_unknown(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> None:
    tz = ZoneInfo(str(settings_obj.attendance_timezone))
    now = datetime.now(tz)
    today = now.date()

    # Two users named "Алексей Смирнов" -> ambiguous!
    user1 = User(id=1, first_name="Алексей", last_name="Смирнов", patronymic="Сергеевич")
    user2 = User(id=2, first_name="Алексей", last_name="Смирнов", patronymic="Игоревич")
    mock_user_repo.find_all_users.return_value = [user1, user2]

    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="OK", problems=[]),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=today,
                rows=[
                    # Ambiguous (matches both users)
                    AttendanceRecognizedRowDTO(raw_name="Смирнов Алексей", time_raw="18:00-19:00"),
                    # Unknown
                    AttendanceRecognizedRowDTO(raw_name="Неизвестный Человек", time_raw="18:00-19:00"),
                ],
            )
        ],
    )
    mock_attendance_repo.create_draft.return_value = AttendanceImport(id=1)

    draft = await service.ingest_attendance_photo(author_tg_id=1, photo_bytes=b"bytes", mime_type="image/jpeg")
    items = draft.dates[0].items

    # Ambiguous -> status="needs_manual_review", matched_user_id=None
    assert items[0].matched_user_id is None
    assert items[0].status == "needs_manual_review"

    # Unknown -> status="needs_manual_review", matched_user_id=None
    assert items[1].matched_user_id is None
    assert items[1].status == "needs_manual_review"


# ============ DRAFT EDIT TESTS ============


def _create_sample_draft(item_id: str = "item-1") -> AttendanceDraftDTO:
    return AttendanceDraftDTO(
        draft_id=10,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 25),
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id=item_id,
                        date=date(2026, 9, 25),
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов Иван",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    )
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_update_draft_item_time(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(
        id=10,
        draft_state_json=sample_draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    updated = await service.update_draft_item_time(
        draft_id=10,
        item_id="item-1",
        start_time=time(17, 0),
        end_time=time(19, 30),
        is_empty_time=False,
    )

    item = updated.dates[0].items[0]
    assert item.duration_minutes == 150
    assert item.status == "recognized"
    mock_attendance_repo.update_draft_state.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_draft_item_participant(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(id=10, draft_state_json=sample_draft.model_dump_json())
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    user5 = User(id=5, first_name="Сергей", last_name="Сидоров")
    mock_user_repo.find_user_by_id.return_value = user5

    updated = await service.update_draft_item_participant(
        draft_id=10,
        item_id="item-1",
        user_id=5,
    )

    item = updated.dates[0].items[0]
    assert item.matched_user_id == 5
    assert item.matched_user_name == "Сидоров Сергей"
    assert item.status == "recognized"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exclude_draft_item(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(id=10, draft_state_json=sample_draft.model_dump_json())
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    updated = await service.exclude_draft_item(draft_id=10, item_id="item-1")

    item = updated.dates[0].items[0]
    assert item.is_excluded is True
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_draft(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_import = AttendanceImport(id=10, draft_state_json="{}")
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    await service.cancel_draft(draft_id=10)

    mock_attendance_repo.cancel_draft.assert_awaited_once_with(mock_db, 10)
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_draft_item_time_item_not_found_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(id=10, draft_state_json=sample_draft.model_dump_json())
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDomainError, match="не найдена в черновике"):
        await service.update_draft_item_time(
            draft_id=10,
            item_id="nonexistent-item",
            start_time=time(18, 0),
            end_time=time(19, 0),
            is_empty_time=False,
        )


@pytest.mark.asyncio
async def test_update_draft_item_participant_user_not_found_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(id=10, draft_state_json=sample_draft.model_dump_json())
    mock_attendance_repo.get_draft_by_id.return_value = mock_import
    mock_user_repo.find_user_by_id.return_value = None

    with pytest.raises(AttendanceUserNotFoundError, match="не найден"):
        await service.update_draft_item_participant(draft_id=10, item_id="item-1", user_id=999)


@pytest.mark.asyncio
async def test_exclude_draft_item_not_found_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(id=10, draft_state_json=sample_draft.model_dump_json())
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDomainError, match="не найдена в черновике"):
        await service.exclude_draft_item(draft_id=10, item_id="nonexistent-item")


@pytest.mark.asyncio
async def test_get_draft_empty_state_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_import = AttendanceImport(id=10, draft_state_json=None)
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDomainError, match="не содержит сохраненного состояния"):
        await service.get_draft(draft_id=10)


@pytest.mark.asyncio
async def test_expired_draft_older_than_ttl_cannot_be_fetched(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    stale_created_at = datetime.now(UTC) - timedelta(hours=25)
    mock_import = AttendanceImport(
        id=10,
        status=AttendanceImportStatus.DRAFT.value,
        created_at=stale_created_at,
        photo_data=b"dummy_photo",
        draft_state_json="{}",
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDraftExpiredError, match="Срок действия черновика истёк"):
        await service.get_draft(draft_id=10)

    mock_attendance_repo.expire_stale_drafts.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
    assert mock_import.status == AttendanceImportStatus.EXPIRED.value
    assert mock_import.photo_data is None


@pytest.mark.asyncio
async def test_expired_draft_by_status_cannot_be_fetched(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_import = AttendanceImport(
        id=10,
        status=AttendanceImportStatus.EXPIRED.value,
        created_at=datetime.now(UTC),
        draft_state_json="{}",
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDraftExpiredError, match="Срок действия черновика истёк"):
        await service.get_draft(draft_id=10)


@pytest.mark.asyncio
async def test_expired_draft_cannot_be_edited_or_confirmed(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    stale_created_at = datetime.now(UTC) - timedelta(hours=25)
    sample_draft = _create_sample_draft("item-1")
    mock_import = AttendanceImport(
        id=10,
        status=AttendanceImportStatus.DRAFT.value,
        created_at=stale_created_at,
        photo_data=b"dummy_photo",
        draft_state_json=sample_draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDraftExpiredError):
        await service.update_draft_item_time(
            draft_id=10,
            item_id="item-1",
            start_time=time(18, 0),
            end_time=time(19, 0),
            is_empty_time=False,
        )

    with pytest.raises(AttendanceDraftExpiredError):
        await service.update_draft_item_participant(draft_id=10, item_id="item-1", user_id=1)

    with pytest.raises(AttendanceDraftExpiredError):
        await service.exclude_draft_item(draft_id=10, item_id="item-1")

    with pytest.raises(AttendanceDraftExpiredError):
        await service.confirm_draft(draft_id=10, admin_tg_id=999)


@pytest.mark.asyncio
async def test_cleanup_stale_drafts(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.expire_stale_drafts.return_value = 3

    count = await service.cleanup_stale_drafts()

    assert count == 3
    mock_attendance_repo.expire_stale_drafts.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_user_monthly_summary_user_not_found_raises(
    service: AttendanceService,
    mock_user_repo: AsyncMock,
) -> None:
    mock_user_repo.find_user_by_id.return_value = None
    with pytest.raises(AttendanceUserNotFoundError, match="не найден"):
        await service.get_user_monthly_summary(user_id=999, year=2026, month=9)


@pytest.mark.asyncio
async def test_ingest_photo_marks_out_of_month_and_already_processed(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
) -> None:
    current_month_date = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    mock_user_repo.find_all_users.return_value = []
    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="OK"),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=date(2025, 1, 1),
                rows=[],
            ),
            AttendanceRecognizedDateBlockDTO(
                date=current_month_date,
                rows=[],
            ),
        ],
    )
    mock_attendance_repo.find_processed_date.side_effect = lambda _db, d: (
        AttendanceDate(id=1, import_id=55, date=d) if d == current_month_date else None
    )
    mock_attendance_repo.create_draft.return_value = AttendanceImport(id=99)

    draft = await service.ingest_attendance_photo(
        author_tg_id=1,
        photo_bytes=b"bytes",
        mime_type="image/jpeg",
    )

    assert len(draft.dates) == 2
    b1, b2 = draft.dates
    assert b1.is_ignored is False
    assert b1.period_warning is not None
    assert "не текущий месяц" in b1.period_warning

    assert b2.is_ignored is True
    assert b2.ignore_reason == "дата уже обработана"
    assert b2.processed_import_id == 55


# ============ CONFIRM DRAFT TESTS ============


@pytest.mark.asyncio
async def test_confirm_draft_happy_path(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    draft = AttendanceDraftDTO(
        draft_id=20,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 25),
                is_ignored=False,
                items=[
                    # User 1 has two rows: 60 min and 120 min -> max duration (120) should be kept!
                    AttendanceDraftItemDTO(
                        item_id="1",
                        date=date(2026, 9, 25),
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    ),
                    AttendanceDraftItemDTO(
                        item_id="2",
                        date=date(2026, 9, 25),
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(20, 0),
                        duration_minutes=120,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    ),
                    # Excluded item -> ignored!
                    AttendanceDraftItemDTO(
                        item_id="3",
                        date=date(2026, 9, 25),
                        raw_name="Петров П.",
                        matched_user_id=2,
                        matched_user_name="Петров",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                        is_excluded=True,
                    ),
                ],
            ),
            # Already processed date -> skipped!
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 26),
                is_ignored=True,
                items=[],
            ),
        ],
    )
    mock_import = AttendanceImport(
        id=20,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import
    mock_attendance_repo.is_date_processed.return_value = False
    mock_user_repo.find_user_by_id.return_value = User(id=1, telegram_id=999888, first_name="Иван")

    added_dates, added_records, skipped = await service.confirm_draft(draft_id=20, admin_tg_id=999)

    assert added_dates == 1
    assert added_records == 1  # Deduplicated from 2 to 1 row
    assert skipped == 1  # 2026-09-26 was ignored

    # Check that save_confirmed_import received the 120 duration row
    mock_attendance_repo.save_confirmed_import.assert_awaited_once()
    call_args = mock_attendance_repo.save_confirmed_import.call_args[1]
    confirmed_dates = call_args["confirmed_dates"]
    assert len(confirmed_dates) == 1
    target_date, rows = confirmed_dates[0]
    assert target_date == date(2026, 9, 25)
    assert len(rows) == 1
    assert rows[0]["user_id"] == 1
    assert rows[0]["duration_minutes"] == 120

    mock_db.commit.assert_awaited_once()

    # Verify notification was sent to user 1
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    notify_kwargs = mock_notification_service.send_attendance_notification.call_args[1]
    assert notify_kwargs["recipient_tg_id"] == 999888
    assert notify_kwargs["import_id"] == 20
    assert "25.09.2026 — 18:00–20:00 (2 ч)" in notify_kwargs["text"]


@pytest.mark.asyncio
async def test_confirm_draft_notification_failure_does_not_abort_confirm(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    draft = AttendanceDraftDTO(
        draft_id=21,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 25),
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="1",
                        date=date(2026, 9, 25),
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(20, 0),
                        duration_minutes=120,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    ),
                ],
            ),
        ],
    )
    mock_import = AttendanceImport(
        id=21,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import
    mock_attendance_repo.is_date_processed.return_value = False
    mock_user_repo.find_user_by_id.return_value = User(id=1, telegram_id=999888, first_name="Иван")
    mock_notification_service.send_attendance_notification.side_effect = RuntimeError("Telegram network error")

    # Confirm should succeed without raising
    added_dates, added_records, skipped = await service.confirm_draft(draft_id=21, admin_tg_id=999)

    assert added_dates == 1
    assert added_records == 1
    mock_attendance_repo.save_confirmed_import.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
    mock_notification_service.send_attendance_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_draft_not_in_draft_status_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_import = AttendanceImport(id=20, status=AttendanceImportStatus.CONFIRMED.value)
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(AttendanceDomainError, match="Черновик уже обработан"):
        await service.confirm_draft(draft_id=20, admin_tg_id=999)


@pytest.mark.asyncio
async def test_confirm_draft_unresolved_rows_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    draft = AttendanceDraftDTO(
        draft_id=20,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 25),
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="1",
                        date=date(2026, 9, 25),
                        raw_name="Неизвестный",
                        matched_user_id=None,
                        matched_user_name=None,
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="needs_manual_review",
                        is_excluded=False,
                    ),
                ],
            ),
        ],
    )
    mock_import = AttendanceImport(
        id=20,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import

    with pytest.raises(
        AttendanceDomainError,
        match="Нельзя подтвердить импорт: в журнале есть неразрешённые строки",
    ):
        await service.confirm_draft(draft_id=20, admin_tg_id=999)


@pytest.mark.asyncio
async def test_confirm_draft_empty_records_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    draft = AttendanceDraftDTO(
        draft_id=20,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date(2026, 9, 25),
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="1",
                        date=date(2026, 9, 25),
                        raw_name="Иванов",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                        is_excluded=True,  # Excluded!
                    ),
                ],
            ),
        ],
    )
    mock_import = AttendanceImport(
        id=20,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    mock_attendance_repo.get_draft_by_id.return_value = mock_import
    mock_attendance_repo.is_date_processed.return_value = False

    with pytest.raises(
        AttendanceDomainError,
        match="Не найдено записей для сохранения: все даты уже обработаны либо все строки исключены",
    ):
        await service.confirm_draft(draft_id=20, admin_tg_id=999)


@pytest.mark.asyncio
async def test_ingest_attendance_photo_preserves_needs_manual_review_and_low_confidence(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> None:
    tz = ZoneInfo(str(settings_obj.attendance_timezone))
    now = datetime.now(tz)
    today = now.date()

    user1 = User(id=1, first_name="Иван", last_name="Иванов")
    user2 = User(id=2, first_name="Петр", last_name="Петров")
    mock_user_repo.find_all_users.return_value = [user1, user2]

    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="ОК", problems=[]),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=today,
                rows=[
                    # Row 1: Low confidence (0.5 < 0.7) even though name is matched
                    AttendanceRecognizedRowDTO(
                        raw_name="Иванов Иван",
                        time_raw="18:00-19:00",
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.5,
                        status="recognized",
                    ),
                    # Row 2: Status needs_manual_review from vision model
                    AttendanceRecognizedRowDTO(
                        raw_name="Петров Петр",
                        time_raw="18:00-19:00",
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.95,
                        status="needs_manual_review",
                    ),
                    # Row 3: Missing/invalid confidence parsed as 0.0
                    AttendanceRecognizedRowDTO(
                        raw_name="Иванов Иван",
                        time_raw="18:00-19:00",
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.0,
                        status="needs_manual_review",
                    ),
                ],
            )
        ],
    )
    mock_draft_record = AttendanceImport(id=43, status=AttendanceImportStatus.DRAFT.value)
    mock_attendance_repo.create_draft.return_value = mock_draft_record

    draft = await service.ingest_attendance_photo(
        author_tg_id=777,
        photo_bytes=b"valid_photo_bytes",
        mime_type="image/jpeg",
    )

    items = draft.dates[0].items
    assert items[0].confidence == 0.5
    assert items[0].status == "needs_manual_review"
    assert items[1].status == "needs_manual_review"
    assert items[2].confidence == 0.0
    assert items[2].status == "needs_manual_review"


@pytest.mark.asyncio
async def test_ingest_attendance_photo_keeps_incomplete_time_for_manual_entry(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> None:
    tz = ZoneInfo(str(settings_obj.attendance_timezone))
    now = datetime.now(tz)
    today = now.date()
    mock_user_repo.find_all_users.return_value = [User(id=1, first_name="Иван", last_name="Иванов")]
    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="ОК", problems=[]),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=today,
                rows=[
                    AttendanceRecognizedRowDTO(
                        raw_name="Иванов Иван",
                        time_raw="8:40-",
                        is_empty_time=False,
                        confidence=0.95,
                        status="needs_manual_review",
                    )
                ],
            )
        ],
    )
    mock_attendance_repo.create_draft.return_value = AttendanceImport(id=44, status=AttendanceImportStatus.DRAFT.value)

    draft = await service.ingest_attendance_photo(author_tg_id=777, photo_bytes=b"photo", mime_type="image/jpeg")

    item = draft.dates[0].items[0]
    assert item.time_raw == "8:40-"
    assert item.start_time is None
    assert item.end_time is None
    assert item.duration_minutes == 0
    assert item.is_empty_time is False
    assert item.is_excluded is False
    assert item.status == "needs_manual_review"


# ============ PHOTO EVIDENCE TESTS ============


@pytest.mark.asyncio
async def test_get_photo_evidence_happy_path(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_import_photo.return_value = (b"image_bytes", "image/png")
    mock_attendance_repo.find_import_photo.return_value = (b"image_bytes", "image/png")
    photo_data, mime = await service.get_photo_evidence(import_id=1)
    assert photo_data == b"image_bytes"
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_get_photo_evidence_missing_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_import_photo.return_value = None
    mock_attendance_repo.find_import_photo.return_value = None
    with pytest.raises(AttendanceDomainError, match="Фото-доказательство не найдено"):
        await service.get_photo_evidence(import_id=999)


@pytest.mark.asyncio
async def test_get_import_photo_with_access_admin_allowed(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    mock_user_repo.find_user_by_telegram_id.return_value = User(id=1, first_name="Админ")
    mock_attendance_repo.get_import_photo.return_value = (b"photo_data", "image/jpeg")
    mock_attendance_repo.get_import_dates.return_value = [date(2026, 9, 20)]

    photo, mime, dates = await service.get_import_photo_with_access(
        import_id=10,
        requester_tg_id=123,
        is_admin=True,
    )
    assert photo == b"photo_data"
    assert mime == "image/jpeg"
    assert dates == [date(2026, 9, 20)]
    mock_attendance_repo.has_user_visit_in_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_visit_months(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.find_user_visit_months.return_value = [(2026, 9), (2026, 6)]
    months = await service.get_user_visit_months(user_id=10)
    assert months == [(2026, 9), (2026, 6)]
    mock_attendance_repo.find_user_visit_months.assert_awaited_once_with(service._db, 10)


@pytest.mark.asyncio
async def test_get_import_photo_with_access_participant_with_visit(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=5, first_name="Студент")
    mock_user_repo.find_user_by_telegram_id.return_value = user
    mock_attendance_repo.has_user_visit_in_import.return_value = True
    mock_attendance_repo.get_import_photo.return_value = (b"student_photo", "image/png")
    mock_attendance_repo.get_import_dates.return_value = [date(2026, 9, 20), date(2026, 9, 21)]

    photo, mime, dates = await service.get_import_photo_with_access(
        import_id=10,
        requester_tg_id=456,
        is_admin=False,
    )
    assert photo == b"student_photo"
    assert mime == "image/png"
    assert dates == [date(2026, 9, 20), date(2026, 9, 21)]
    mock_attendance_repo.has_user_visit_in_import.assert_awaited_once_with(service._db, 5, 10)


@pytest.mark.asyncio
async def test_get_import_photo_with_access_participant_without_visit_denied(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=7, first_name="Чужой")
    mock_user_repo.find_user_by_telegram_id.return_value = user
    mock_attendance_repo.has_user_visit_in_import.return_value = False

    with pytest.raises(AttendancePhotoAccessDeniedError, match="Фото недоступно: посещение удалено."):
        await service.get_import_photo_with_access(
            import_id=10,
            requester_tg_id=789,
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_get_import_photo_with_access_photo_not_found(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=5, first_name="Студент")
    mock_user_repo.find_user_by_telegram_id.return_value = user
    mock_attendance_repo.has_user_visit_in_import.return_value = True
    mock_attendance_repo.get_import_photo.return_value = None

    with pytest.raises(AttendancePhotoNotFoundError, match="Фотография импорта не найдена."):
        await service.get_import_photo_with_access(
            import_id=10,
            requester_tg_id=456,
            is_admin=False,
        )


@pytest.mark.asyncio
async def test_edit_visit_time_success(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    user = User(id=1, telegram_id=111, first_name="Иван")
    rec = AttendanceRecord(
        id=10,
        import_id=5,
        user_id=1,
        date=date(2026, 9, 20),
        start_time=time(12, 0),
        end_time=time(14, 0),
        duration_minutes=120,
        user=user,
    )
    mock_attendance_repo.find_record_by_id.return_value = rec
    updated_rec = AttendanceRecord(
        id=10,
        import_id=5,
        user_id=1,
        date=date(2026, 9, 20),
        start_time=time(12, 30),
        end_time=time(15, 0),
        duration_minutes=150,
        user=user,
    )
    mock_attendance_repo.update_record_time.return_value = updated_rec
    mock_user_repo.find_user_by_id.return_value = user

    result = await service.edit_visit_time(
        record_id=10,
        new_start_time=time(12, 30),
        new_end_time=time(15, 0),
        admin_tg_id=999,
        admin_name="Админ",
    )

    assert result.duration_minutes == 150
    mock_attendance_repo.update_record_time.assert_awaited_once_with(
        db=mock_db,
        record_id=10,
        start_time=time(12, 30),
        end_time=time(15, 0),
        duration_minutes=150,
    )
    mock_attendance_repo.add_change_history.assert_awaited_once()
    history_kwargs = mock_attendance_repo.add_change_history.call_args[1]
    assert history_kwargs["action"] == "edit_time"
    assert history_kwargs["old_value"] == "12:00–14:00"
    assert history_kwargs["new_value"] == "12:30–15:00"
    mock_db.commit.assert_awaited_once()

    # Notification sent
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    notify_kwargs = mock_notification_service.send_attendance_notification.call_args[1]
    assert notify_kwargs["recipient_tg_id"] == 111
    assert notify_kwargs["import_id"] == 5
    assert "12:00–14:00 → 12:30–15:00" in notify_kwargs["text"]


@pytest.mark.asyncio
async def test_edit_visit_time_same_time_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    rec = AttendanceRecord(
        id=10,
        import_id=5,
        user_id=1,
        date=date(2026, 9, 20),
        start_time=time(12, 0),
        end_time=time(14, 0),
        duration_minutes=120,
    )
    mock_attendance_repo.find_record_by_id.return_value = rec

    with pytest.raises(AttendanceDomainError, match="Новое время совпадает с текущим"):
        await service.edit_visit_time(
            record_id=10,
            new_start_time=time(12, 0),
            new_end_time=time(14, 0),
            admin_tg_id=999,
            admin_name="Админ",
        )


@pytest.mark.asyncio
async def test_delete_visit_success(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    user = User(id=1, telegram_id=222, first_name="Иван")
    rec = AttendanceRecord(
        id=10,
        import_id=5,
        user_id=1,
        date=date(2026, 9, 20),
        start_time=time(12, 0),
        end_time=time(14, 0),
        duration_minutes=120,
    )
    mock_attendance_repo.find_record_by_id.return_value = rec
    mock_user_repo.find_user_by_id.return_value = user

    uid, v_date, old_str = await service.delete_visit(
        record_id=10,
        admin_tg_id=999,
        admin_name="Админ",
    )

    assert uid == 1
    assert v_date == date(2026, 9, 20)
    assert old_str == "12:00–14:00"
    mock_attendance_repo.delete_record.assert_awaited_once_with(mock_db, 10)
    mock_attendance_repo.add_change_history.assert_awaited_once()
    assert mock_attendance_repo.add_change_history.call_args[1]["action"] == "delete"
    mock_db.commit.assert_awaited_once()

    # Notification sent (without import button)
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    notify_kwargs = mock_notification_service.send_attendance_notification.call_args[1]
    assert notify_kwargs["recipient_tg_id"] == 222
    assert notify_kwargs["import_id"] is None
    assert "удалено" in notify_kwargs["text"]


@pytest.mark.asyncio
async def test_create_manual_visit_success(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    user = User(id=1, telegram_id=333, first_name="Иван")
    mock_user_repo.find_user_by_id.return_value = user
    mock_attendance_repo.has_user_visit_on_date.return_value = False
    created_rec = AttendanceRecord(
        id=50,
        user_id=1,
        date=date(2026, 9, 10),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        source="manual",
    )
    mock_attendance_repo.create_manual_record.return_value = created_rec

    result = await service.create_manual_visit(
        user_id=1,
        visit_date=date(2026, 9, 10),
        start_time=time(18, 0),
        end_time=time(20, 0),
        admin_tg_id=999,
        admin_name="Админ",
    )

    assert result.source == "manual"
    mock_attendance_repo.create_manual_record.assert_awaited_once()
    mock_attendance_repo.add_change_history.assert_awaited_once()
    assert mock_attendance_repo.add_change_history.call_args[1]["action"] == "manual_add"
    mock_db.commit.assert_awaited_once()

    # Notification sent
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    notify_kwargs = mock_notification_service.send_attendance_notification.call_args[1]
    assert notify_kwargs["recipient_tg_id"] == 333
    assert "Добавлено посещение" in notify_kwargs["text"]


@pytest.mark.asyncio
async def test_create_manual_visit_duplicate_date_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=1, telegram_id=333, first_name="Иван")
    mock_user_repo.find_user_by_id.return_value = user
    mock_attendance_repo.has_user_visit_on_date.return_value = True

    with pytest.raises(AttendanceDomainError, match="уже есть посещение"):
        await service.create_manual_visit(
            user_id=1,
            visit_date=date(2026, 9, 10),
            start_time=time(18, 0),
            end_time=time(20, 0),
            admin_tg_id=999,
            admin_name="Админ",
        )


@pytest.mark.asyncio
async def test_get_record_change_history(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_recent_change_history.return_value = [
        AttendanceChangeHistory(
            id=1,
            user_id=1,
            record_id=10,
            visit_date=date(2026, 9, 20),
            action="edit_time",
            old_value="12:00-14:00",
            new_value="12:30-15:00",
            admin_tg_id=999,
            admin_name="Админ",
        )
    ]
    history = await service.get_record_change_history(user_id=1, visit_date=date(2026, 9, 20))
    assert len(history) == 1
    assert history[0].action == "edit_time"


# ============ MONTHLY REPORTS & STATS TESTS ============


@pytest.mark.asyncio
async def test_get_user_monthly_summary(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=10, first_name="Иван", last_name="Иванов")
    user2 = User(id=20, first_name="Петр", last_name="Петров")
    user3 = User(id=30, first_name="Сидор", last_name="Сидоров")
    mock_user_repo.find_user_by_id.return_value = user
    mock_attendance_repo.get_rankings_for_period.return_value = [
        (user, 180, 2),
        (user2, 120, 1),
        (user3, 60, 1),
    ]

    summary = await service.get_user_monthly_summary(user_id=10, year=2026, month=9)

    assert summary.user_id == 10
    assert summary.user_full_name == "Иванов Иван"
    assert summary.total_minutes == 180
    assert summary.rank == 1
    assert summary.total_participants == 3
    assert summary.visits_count == 2


@pytest.mark.asyncio
async def test_get_user_monthly_summary_without_visits(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    user = User(id=99, first_name="Новый", last_name="Участник")
    active_user = User(id=1, first_name="Иван", last_name="Иванов")
    mock_user_repo.find_user_by_id.return_value = user
    mock_attendance_repo.get_rankings_for_period.return_value = [
        (active_user, 180, 2),
    ]

    summary = await service.get_user_monthly_summary(user_id=99, year=2026, month=9)

    assert summary.user_id == 99
    assert summary.user_full_name == "Участник Новый"
    assert summary.total_minutes == 0
    assert summary.rank == 0
    assert summary.total_participants == 1
    assert summary.visits_count == 0


@pytest.mark.asyncio
async def test_get_monthly_rankings_competition_rule(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    u1 = User(id=1, first_name="Алексей", last_name="Алексеев")
    u2 = User(id=2, first_name="Борис", last_name="Борисов")
    u3 = User(id=3, first_name="Василий", last_name="Васильев")
    u4 = User(id=4, first_name="Григорий", last_name="Григорьев")
    mock_attendance_repo.get_rankings_for_period.return_value = [
        (u1, 200, 2),
        (u2, 150, 2),
        (u3, 150, 1),
        (u4, 80, 1),
    ]

    rankings = await service.get_monthly_rankings(year=2026, month=9)

    assert [r.rank for r in rankings] == [1, 2, 2, 4]
    assert [r.user_id for r in rankings] == [1, 2, 3, 4]


class _FrozenDateTime(datetime):
    """Datetime whose now() is Sunday 2026-10-04 19:30 UTC, i.e. Monday 00:30 in Yekaterinburg."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        moment = datetime(2026, 10, 4, 19, 30, tzinfo=UTC)
        return moment.astimezone(tz) if tz else moment


def _ranking_rows(minutes_and_visits: list[tuple[int, int]]) -> list[tuple[User, int, int]]:
    return [
        (User(id=index, first_name=f"Имя{index}", last_name=f"Фамилия{index}"), minutes, visits)
        for index, (minutes, visits) in enumerate(minutes_and_visits, start=1)
    ]


@pytest.mark.parametrize(
    ("today", "expected_monday", "expected_saturday"),
    [
        (date(2026, 9, 28), date(2026, 9, 28), date(2026, 10, 3)),
        (date(2026, 9, 29), date(2026, 9, 28), date(2026, 10, 3)),
        (date(2026, 10, 3), date(2026, 9, 28), date(2026, 10, 3)),
        (date(2026, 10, 4), date(2026, 9, 28), date(2026, 10, 3)),
        (date(2026, 10, 5), date(2026, 10, 5), date(2026, 10, 10)),
        (date(2025, 12, 31), date(2025, 12, 29), date(2026, 1, 3)),
    ],
)
def test_get_attendance_week_bounds_is_monday_to_saturday(
    today: date,
    expected_monday: date,
    expected_saturday: date,
) -> None:
    assert AttendanceService.get_attendance_week_bounds(today) == (expected_monday, expected_saturday)


def test_get_attendance_week_bounds_uses_yekaterinburg_business_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pybot.services.attendance_service.datetime", _FrozenDateTime)

    # Sunday 19:30 UTC is already Monday in Yekaterinburg, so the new week has started.
    assert AttendanceService.get_attendance_week_bounds() == (date(2026, 10, 5), date(2026, 10, 10))


@pytest.mark.asyncio
async def test_get_week_top_limits_rows_to_top_five_and_finds_viewer(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pybot.services.attendance_service.datetime", _FrozenDateTime)
    mock_attendance_repo.get_rankings_for_period.return_value = _ranking_rows(
        [(600, 4), (500, 3), (500, 2), (400, 2), (300, 1), (200, 1), (100, 1)]
    )

    top = await service.get_week_top(user_id=7)

    mock_attendance_repo.get_rankings_for_period.assert_awaited_once_with(
        service._db, date(2026, 10, 5), date(2026, 10, 10)
    )
    assert (top.period_start, top.period_end) == (date(2026, 10, 5), date(2026, 10, 10))
    assert [row.rank for row in top.rows] == [1, 2, 2, 4, 5]
    assert top.total_participants == 7
    assert top.current_user is not None
    assert (top.current_user.rank, top.current_user.total_minutes) == (7, 100)


@pytest.mark.asyncio
async def test_get_week_top_keeps_everyone_tied_for_fifth_place(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_rankings_for_period.return_value = _ranking_rows(
        [(600, 1), (500, 1), (400, 1), (300, 1), (200, 1), (200, 1), (200, 1), (100, 1)]
    )

    top = await service.get_week_top(user_id=99)

    assert [row.rank for row in top.rows] == [1, 2, 3, 4, 5, 5, 5]
    assert top.total_participants == 8
    assert top.current_user is None


@pytest.mark.asyncio
async def test_get_week_top_without_participants(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_rankings_for_period.return_value = []

    top = await service.get_week_top(user_id=1)

    assert top.rows == []
    assert top.total_participants == 0
    assert top.current_user is None


@pytest.mark.asyncio
async def test_get_week_top_ignores_participants_without_minutes(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_rankings_for_period.return_value = _ranking_rows([(90, 1), (0, 1)])

    top = await service.get_week_top(user_id=2)

    assert [row.user_id for row in top.rows] == [1]
    assert top.total_participants == 1
    assert top.current_user is None


@pytest.mark.asyncio
async def test_get_month_top_uses_calendar_month_bounds(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.get_rankings_for_period.return_value = _ranking_rows([(120, 2), (60, 1)])

    top = await service.get_month_top(user_id=2, year=2026, month=2)

    mock_attendance_repo.get_rankings_for_period.assert_awaited_once_with(
        service._db, date(2026, 2, 1), date(2026, 2, 28)
    )
    assert (top.period_start, top.period_end) == (date(2026, 2, 1), date(2026, 2, 28))
    assert [row.rank for row in top.rows] == [1, 2]
    assert top.current_user is not None
    assert top.current_user.user_id == 2


@pytest.mark.asyncio
async def test_get_attendance_months_delegates_to_repository(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.find_months_with_visits.return_value = [(2026, 9), (2026, 6)]

    assert await service.get_attendance_months() == [(2026, 9), (2026, 6)]
    mock_attendance_repo.find_months_with_visits.assert_awaited_once_with(service._db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        ((2026, 9), ((2026, 8), None)),
        ((2026, 8), ((2026, 6), (2026, 9))),
        ((2026, 6), (None, (2026, 8))),
        ((2026, 7), ((2026, 6), (2026, 8))),
    ],
)
async def test_find_adjacent_attendance_months_skips_months_without_visits(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    shown: tuple[int, int],
    expected: tuple[tuple[int, int] | None, tuple[int, int] | None],
) -> None:
    monkeypatch.setattr("pybot.services.attendance_service.datetime", _FrozenDateTime)
    mock_attendance_repo.find_months_with_visits.return_value = [(2026, 9), (2026, 8), (2026, 6)]

    # "Now" is October 2026, which has no visits yet; September stays the latest reachable month.
    assert await service.find_adjacent_attendance_months(*shown) == expected


@pytest.mark.asyncio
async def test_find_adjacent_attendance_months_never_pages_into_the_future(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pybot.services.attendance_service.datetime", _FrozenDateTime)
    mock_attendance_repo.find_months_with_visits.return_value = [(2026, 12), (2026, 10), (2026, 9)]

    previous_month, next_month = await service.find_adjacent_attendance_months(2026, 9)

    assert previous_month is None
    assert next_month == (2026, 10)


@pytest.mark.asyncio
async def test_get_user_monthly_visits(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    user = User(id=10, first_name="Иван", last_name="Иванов")
    record = AttendanceRecord(
        id=1,
        user_id=10,
        user=user,
        date=date(2026, 9, 25),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        source="photo_ai",
        created_at=datetime(2026, 9, 25, 20, 0),
    )
    mock_attendance_repo.find_user_attendance_in_month.return_value = [record]

    visits = await service.get_user_monthly_visits(user_id=10, year=2026, month=9)

    assert len(visits) == 1
    assert visits[0].id == 1
    assert visits[0].duration_minutes == 120
    assert visits[0].user_full_name == "Иванов Иван"


@pytest.mark.asyncio
async def test_get_monthly_report(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    user1 = User(id=1, first_name="Иван", last_name="Иванов")
    user2 = User(id=2, first_name="Петр", last_name="Петров")
    mock_attendance_repo.get_rankings_for_period.return_value = [
        (user1, 300, 3),
        (user2, 120, 1),
    ]

    report = await service.get_monthly_report(year=2026, month=9, detailed=False)

    assert report.year == 2026
    assert report.month == 9
    assert report.total_club_minutes == 420
    assert report.active_participants_count == 2
    assert report.average_minutes_per_participant == 210
    assert len(report.rankings) == 2
    assert report.rankings[0].user_full_name == "Иванов Иван"
    assert report.rankings[0].rank == 1
    assert report.rankings[1].rank == 2


@pytest.mark.asyncio
async def test_di_container_resolves_attendance_components(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: AppSettings,
) -> None:
    settings_obj.polza_api_key = SecretStr("dummy_key")
    settings_obj.bot_token_test = "123456:TEST_TOKEN"
    settings_obj.telegram_proxy_url = None

    class FakeBot:
        def __init__(self, token: str, session=None, **_: object) -> None:
            self.token = token
            self.session = MagicMock()
            self.session.close = AsyncMock()

    class FakeRedis:
        async def aclose(self) -> None:
            pass

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_session = AsyncMock()

    class FakeSessionContext:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(di_containers, "Bot", FakeBot)
    monkeypatch.setattr(di_containers.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    monkeypatch.setattr(di_containers, "create_database_engine", lambda *_: fake_engine)
    monkeypatch.setattr(di_containers, "async_sessionmaker", lambda *args, **kwargs: lambda: FakeSessionContext())

    container = await di_containers.setup_container()
    try:
        repo = await container.get(AttendanceRepository)
        port = await container.get(AttendanceRecognitionPort)
        assert isinstance(repo, AttendanceRepository)
        assert isinstance(port, PolzaVisionAdapter)

        async with container() as request_container:
            service = await request_container.get(AttendanceService)
            assert isinstance(service, AttendanceService)
    finally:
        await container.close()


@pytest.mark.asyncio
async def test_ingest_photo_skips_participant_with_existing_manual_visit(
    service: AttendanceService,
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> None:
    tz = ZoneInfo(str(settings_obj.attendance_timezone))
    now = datetime.now(tz)
    today = now.date()

    user1 = User(id=1, first_name="Иван", last_name="Иванов")
    user2 = User(id=2, first_name="Петр", last_name="Петров")
    mock_user_repo.find_all_users.return_value = [user1, user2]

    # user1 already has a manual record on today
    manual_rec = AttendanceRecord(
        id=101,
        user_id=1,
        date=today,
        start_time=time(10, 0),
        end_time=time(12, 0),
        duration_minutes=120,
        source="manual",
        import_id=None,
    )

    async def fake_find_user_rec(_db, uid, dt_val):
        if uid == 1 and dt_val == today:
            return manual_rec
        return None

    mock_attendance_repo.find_user_record_on_date.side_effect = fake_find_user_rec

    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="Четкий снимок", problems=[]),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=today,
                rows=[
                    AttendanceRecognizedRowDTO(
                        raw_name="Иванов Иван",
                        time_raw="18:00-20:00",
                        confidence=0.95,
                    ),
                    AttendanceRecognizedRowDTO(
                        raw_name="Петров Петр",
                        time_raw="18:00-20:00",
                        confidence=0.95,
                    ),
                ],
            )
        ],
    )

    mock_draft_record = AttendanceImport(id=55, status=AttendanceImportStatus.DRAFT.value)
    mock_attendance_repo.create_draft.return_value = mock_draft_record

    draft = await service.ingest_attendance_photo(
        author_tg_id=777,
        photo_bytes=b"valid_photo_bytes",
        mime_type="image/jpeg",
    )

    items = draft.dates[0].items
    assert len(items) == 2

    # Item for user1 must be excluded with reason
    user1_item = next(it for it in items if it.matched_user_id == 1)
    assert user1_item.is_excluded is True
    assert "У участника Иванов Иван уже есть посещение за" in (user1_item.exclusion_reason or "")
    assert "(добавлено вручную)" in (user1_item.exclusion_reason or "")

    # Item for user2 must NOT be excluded
    user2_item = next(it for it in items if it.matched_user_id == 2)
    assert user2_item.is_excluded is False
    assert user2_item.exclusion_reason is None

    # Now verify confirming draft: excluded item is NOT persisted
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=55,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    added_dates, added_records, skipped = await service.confirm_draft(draft_id=55, admin_tg_id=999)

    assert added_dates == 1
    assert added_records == 1
    confirmed_call = mock_attendance_repo.save_confirmed_import.call_args[1]
    saved_dates = confirmed_call["confirmed_dates"]
    assert len(saved_dates) == 1
    assert len(saved_dates[0][1]) == 1
    assert saved_dates[0][1][0]["user_id"] == 2


@pytest.mark.asyncio
async def test_update_draft_item_participant_marks_excluded_if_duplicate(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    visit_date = date(2026, 9, 25)
    item = AttendanceDraftItemDTO(
        item_id="i1",
        date=visit_date,
        raw_name="Неизвестный",
        matched_user_id=None,
        matched_user_name=None,
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        confidence=0.5,
        status="needs_manual_review",
    )
    date_block = AttendanceDraftDateBlockDTO(date=visit_date, items=[item])
    draft = AttendanceDraftDTO(
        draft_id=60,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[date_block],
        status="DRAFT",
    )
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=60, status="DRAFT", draft_state_json=draft.model_dump_json()
    )

    user = User(id=42, first_name="Сергей", last_name="Сергеев")
    mock_user_repo.find_user_by_id.return_value = user

    # User 42 already has a manual record
    mock_attendance_repo.find_user_record_on_date.return_value = AttendanceRecord(
        id=12, user_id=42, date=visit_date, source="manual", import_id=None
    )

    updated_draft = await service.update_draft_item_participant(draft_id=60, item_id="i1", user_id=42)
    updated_item = updated_draft.dates[0].items[0]

    assert updated_item.matched_user_id == 42
    assert updated_item.is_excluded is True
    assert "У участника Сергеев Сергей уже есть посещение за 25.09.2026 (добавлено вручную)." in (
        updated_item.exclusion_reason or ""
    )


@pytest.mark.asyncio
async def test_get_user_monthly_visits_populates_is_time_modified(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    user = User(id=10, first_name="Иван", last_name="Иванов")
    record1 = AttendanceRecord(
        id=1,
        user_id=10,
        user=user,
        date=date(2026, 9, 24),
        start_time=time(14, 0),
        end_time=time(16, 0),
        duration_minutes=120,
        is_empty_time=False,
        source="photo_ai",
        import_id=10,
        created_at=datetime(2026, 9, 24, 20, 0),
    )
    record2 = AttendanceRecord(
        id=2,
        user_id=10,
        user=user,
        date=date(2026, 9, 25),
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        source="manual",
        import_id=None,
        created_at=datetime(2026, 9, 25, 20, 0),
    )
    mock_attendance_repo.find_user_attendance_in_month.return_value = [record1, record2]
    # Record 1 had its time modified by admin
    mock_attendance_repo.find_modified_record_ids.return_value = {1}

    visits = await service.get_user_monthly_visits(user_id=10, year=2026, month=9)

    assert len(visits) == 2
    assert visits[0].id == 1
    assert visits[0].is_time_modified is True
    assert visits[1].id == 2
    assert visits[1].is_time_modified is False


@pytest.mark.asyncio
async def test_has_user_visit_on_date_public_method(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_db: AsyncMock,
) -> None:
    mock_attendance_repo.has_user_visit_on_date.return_value = True
    res = await service.has_user_visit_on_date(user_id=7, visit_date=date(2026, 9, 20))

    assert res is True
    mock_attendance_repo.has_user_visit_on_date.assert_awaited_once_with(mock_db, 7, date(2026, 9, 20))


@pytest.mark.asyncio
async def test_confirm_draft_all_excluded_date_not_added_and_not_blocked(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    date1 = date(2026, 9, 25)
    date2 = date(2026, 9, 26)
    draft = AttendanceDraftDTO(
        draft_id=30,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            # Date 1: all items are excluded -> 0 records to add
            AttendanceDraftDateBlockDTO(
                date=date1,
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="item_1",
                        date=date1,
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                        is_excluded=True,
                        exclusion_reason="Уже есть посещение",
                    )
                ],
            ),
            # Date 2: has valid item
            AttendanceDraftDateBlockDTO(
                date=date2,
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="item_2",
                        date=date2,
                        raw_name="Петров П.",
                        matched_user_id=2,
                        matched_user_name="Петров",
                        start_time=time(18, 0),
                        end_time=time(20, 0),
                        duration_minutes=120,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                        is_excluded=False,
                    )
                ],
            ),
        ],
    )
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=30,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    mock_user_repo.find_user_by_id.return_value = User(id=2, telegram_id=888, first_name="Петр")

    added_dates, added_records, skipped = await service.confirm_draft(draft_id=30, admin_tg_id=999)

    assert added_dates == 1
    assert added_records == 1
    assert skipped == 1  # date1 was skipped

    # save_confirmed_import should only receive date2, NOT date1
    mock_attendance_repo.save_confirmed_import.assert_awaited_once()
    confirmed_call = mock_attendance_repo.save_confirmed_import.call_args[1]
    saved_dates = confirmed_call["confirmed_dates"]
    assert len(saved_dates) == 1
    assert saved_dates[0][0] == date2

    # Notifications should only be sent for user 2 on date2
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    notif_call = mock_notification_service.send_attendance_notification.call_args[1]
    assert notif_call["recipient_tg_id"] == 888


@pytest.mark.asyncio
async def test_confirm_draft_skipped_by_repository_if_records_already_exist(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_notification_service: AsyncMock,
) -> None:
    date1 = date(2026, 9, 25)
    date2 = date(2026, 9, 26)
    draft = AttendanceDraftDTO(
        draft_id=31,
        author_tg_id=100,
        created_at=datetime(2026, 9, 25, 12, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=100,
        photo_checksum="sha",
        status=AttendanceImportStatus.DRAFT.value,
        dates=[
            AttendanceDraftDateBlockDTO(
                date=date1,
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="item_1",
                        date=date1,
                        raw_name="Иванов И.",
                        matched_user_id=1,
                        matched_user_name="Иванов",
                        start_time=time(18, 0),
                        end_time=time(19, 0),
                        duration_minutes=60,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    )
                ],
            ),
            AttendanceDraftDateBlockDTO(
                date=date2,
                is_ignored=False,
                items=[
                    AttendanceDraftItemDTO(
                        item_id="item_2",
                        date=date2,
                        raw_name="Петров П.",
                        matched_user_id=2,
                        matched_user_name="Петров",
                        start_time=time(18, 0),
                        end_time=time(20, 0),
                        duration_minutes=120,
                        is_empty_time=False,
                        confidence=0.9,
                        status="recognized",
                    )
                ],
            ),
        ],
    )
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=31,
        status=AttendanceImportStatus.DRAFT.value,
        draft_state_json=draft.model_dump_json(),
    )
    # Repository simulates date1 rows already having visits in DB, so only date2 is actually saved
    valid_date2_rows = [
        {
            "user_id": 2,
            "start_time": time(18, 0),
            "end_time": time(20, 0),
            "duration_minutes": 120,
            "is_empty_time": False,
            "source": "photo_ai",
        }
    ]
    mock_attendance_repo.save_confirmed_import.side_effect = None
    mock_attendance_repo.save_confirmed_import.return_value = [(date2, valid_date2_rows)]
    mock_user_repo.find_user_by_id.return_value = User(id=2, telegram_id=888, first_name="Петр")

    added_dates, added_records, skipped = await service.confirm_draft(draft_id=31, admin_tg_id=999)

    assert added_dates == 1
    assert added_records == 1
    assert skipped == 1  # date1 was skipped because 0 records were saved

    # Notifications only sent for user 2
    mock_notification_service.send_attendance_notification.assert_awaited_once()
    assert mock_notification_service.send_attendance_notification.call_args[1]["recipient_tg_id"] == 888


@pytest.mark.asyncio
async def test_reassign_excluded_draft_item_to_user_without_duplicate_clears_exclusion(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
) -> None:
    visit_date = date(2026, 9, 25)
    item = AttendanceDraftItemDTO(
        item_id="i1",
        date=visit_date,
        raw_name="Иванов И.",
        matched_user_id=10,
        matched_user_name="Иванов Иван",
        start_time=time(18, 0),
        end_time=time(20, 0),
        duration_minutes=120,
        is_empty_time=False,
        confidence=0.9,
        status="recognized",
        is_excluded=True,
        exclusion_reason="У участника Иванов Иван уже есть посещение за 25.09.2026.",
    )
    date_block = AttendanceDraftDateBlockDTO(date=visit_date, items=[item])
    draft = AttendanceDraftDTO(
        draft_id=70,
        author_tg_id=1,
        created_at=datetime(2026, 9, 25, 20, 0),
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha",
        dates=[date_block],
        status="DRAFT",
    )
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=70, status="DRAFT", draft_state_json=draft.model_dump_json()
    )

    user20 = User(id=20, first_name="Сидор", last_name="Сидоров")
    mock_user_repo.find_user_by_id.return_value = user20
    # User 20 does NOT have a visit on visit_date
    mock_attendance_repo.find_user_record_on_date.return_value = None

    # Reassign using update_draft_item_participant (or alias update_draft_item_user)
    updated_draft = await service.update_draft_item_user(draft_id=70, item_id="i1", user_id=20)
    updated_item = updated_draft.dates[0].items[0]

    assert updated_item.matched_user_id == 20
    assert updated_item.matched_user_name == "Сидоров Сидор"
    assert updated_item.is_excluded is False
    assert updated_item.exclusion_reason is None
    assert updated_item.status == "recognized"

    # Now if reassigned to user 30 who DOES have a duplicate visit
    user30 = User(id=30, first_name="Алексей", last_name="Алексеев")
    mock_user_repo.find_user_by_id.return_value = user30
    mock_attendance_repo.find_user_record_on_date.return_value = AttendanceRecord(
        id=55, user_id=30, date=visit_date, source="manual", import_id=None
    )

    # Prepare draft state with current state
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=70, status="DRAFT", draft_state_json=updated_draft.model_dump_json()
    )
    updated_draft2 = await service.update_draft_item_participant(draft_id=70, item_id="i1", user_id=30)
    updated_item2 = updated_draft2.dates[0].items[0]

    assert updated_item2.matched_user_id == 30
    assert updated_item2.is_excluded is True
    assert "У участника Алексеев Алексей уже есть посещение" in (updated_item2.exclusion_reason or "")

    # Now reset user (user_id=None)
    mock_attendance_repo.get_draft_by_id.return_value = AttendanceImport(
        id=70, status="DRAFT", draft_state_json=updated_draft2.model_dump_json()
    )
    reset_draft = await service.update_draft_item_user(draft_id=70, item_id="i1", user_id=None)
    reset_item = reset_draft.dates[0].items[0]
    assert reset_item.matched_user_id is None
    assert reset_item.matched_user_name is None
    assert reset_item.status == "needs_manual_review"
    assert reset_item.is_excluded is False
    assert reset_item.exclusion_reason is None
