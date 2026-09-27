"""Unit tests for AttendanceService orchestration and use cases."""

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from dishka import make_async_container
from pydantic import SecretStr

from pybot.core.config import AppSettings
from pybot.db.models import AttendanceDate, AttendanceImport, AttendanceImportStatus, AttendanceRecord, User
from pybot.di import containers as di_containers
from pybot.domain.exceptions import (
    AttendanceDomainError,
    AttendanceDraftExpiredError,
    AttendancePhotoEmptyError,
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
from pybot.services.ports import AttendanceRecognitionPort


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
def service(
    mock_db: AsyncMock,
    mock_attendance_repo: AsyncMock,
    mock_user_repo: AsyncMock,
    mock_recognition_port: AsyncMock,
    settings_obj: AppSettings,
) -> AttendanceService:
    settings_obj.attendance_timezone = "Asia/Yekaterinburg"
    return AttendanceService(
        db=mock_db,
        attendance_repo=mock_attendance_repo,
        user_repo=mock_user_repo,
        recognition_port=mock_recognition_port,
        settings=settings_obj,
    )


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
    today = date(now.year, now.month, 15)

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
    assert len(date_block.items) == 1

    # Check item 1: Иванов Иван, 120 min duration, recognized
    item1 = date_block.items[0]
    assert item1.matched_user_id == 1
    assert item1.duration_minutes == 120
    assert item1.status == "recognized"
    assert item1.is_empty_time is False
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
    today = date(now.year, now.month, 10)

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
    mock_user_repo.find_all_users.return_value = []
    mock_recognition_port.recognize_attendance_sheet.return_value = AttendanceRecognitionResultDTO(
        quality=AttendanceQualityDTO(is_good=True, comment="OK"),
        dates=[
            AttendanceRecognizedDateBlockDTO(
                date=date(2025, 1, 1),
                rows=[],
            ),
            AttendanceRecognizedDateBlockDTO(
                date=date(2026, 9, 15),
                rows=[],
            ),
        ],
    )
    mock_attendance_repo.find_processed_date.side_effect = lambda _db, d: (
        AttendanceDate(id=1, import_id=55, date=d) if d == date(2026, 9, 15) else None
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
    today = date(now.year, now.month, 15)

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


# ============ PHOTO EVIDENCE TESTS ============


@pytest.mark.asyncio
async def test_get_photo_evidence_happy_path(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.find_import_photo.return_value = (b"image_bytes", "image/png")
    photo_data, mime = await service.get_photo_evidence(import_id=1)
    assert photo_data == b"image_bytes"
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_get_photo_evidence_missing_raises(
    service: AttendanceService,
    mock_attendance_repo: AsyncMock,
) -> None:
    mock_attendance_repo.find_import_photo.return_value = None
    with pytest.raises(AttendanceDomainError, match="Фото-доказательство не найдено"):
        await service.get_photo_evidence(import_id=999)


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
    mock_attendance_repo.get_monthly_rankings.return_value = [
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
    mock_attendance_repo.get_monthly_rankings.return_value = [
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
    mock_attendance_repo.get_monthly_rankings.return_value = [
        (u1, 200, 2),
        (u2, 150, 2),
        (u3, 150, 1),
        (u4, 80, 1),
    ]

    rankings = await service.get_monthly_rankings(year=2026, month=9)

    assert [r.rank for r in rankings] == [1, 2, 2, 4]
    assert [r.user_id for r in rankings] == [1, 2, 3, 4]


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
    mock_attendance_repo.get_monthly_rankings.return_value = [
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
