"""Unit tests for AttendanceRepository."""

from collections.abc import AsyncGenerator
from datetime import date, datetime, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import BigInteger, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from pybot.db.models import (
    AttendanceDate,
    AttendanceImport,
    AttendanceImportStatus,
    AttendanceRecord,
    Base,
    User,
)
from pybot.domain.exceptions import AttendanceDraftNotFoundError
from pybot.infrastructure.attendance_repository import (
    AttendanceRepository,
    _is_attendance_record_duplicate_error,
)


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type: Any, _compiler: Any, **_kw: Any) -> str:
    return "INTEGER"


@pytest.fixture
def repository() -> AttendanceRepository:
    return AttendanceRepository()


@pytest_asyncio.fixture
async def sqlite_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_draft_persists_new_draft(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    draft = await repository.create_draft(
        db=db,
        author_tg_id=123456,
        photo_data=b"image_bytes",
        photo_mime_type="image/jpeg",
        photo_file_size=1024,
        photo_checksum="sha256_dummy",
        raw_ai_json='{"quality": {"isGood": true}}',
        draft_state_json='{"dates": []}',
    )
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    assert draft.author_tg_id == 123456
    assert draft.status == AttendanceImportStatus.DRAFT.value
    assert draft.photo_data == b"image_bytes"


@pytest.mark.asyncio
async def test_find_draft_by_id_returns_none_when_missing(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    result = await repository.find_draft_by_id(db, import_id=999)

    assert result is None


@pytest.mark.asyncio
async def test_find_draft_by_id_returns_entity_when_found(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    mock_draft = AttendanceImport(id=42, author_tg_id=123)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = mock_draft
    db.execute.return_value = result_mock

    result = await repository.find_draft_by_id(db, import_id=42)

    assert result is not None
    assert result.id == 42


@pytest.mark.asyncio
async def test_find_import_photo_months_and_recorded_dates(
    repository: AttendanceRepository, sqlite_session: AsyncSession
) -> None:
    confirmed_with_photo = AttendanceImport(
        author_tg_id=1,
        status=AttendanceImportStatus.CONFIRMED.value,
        photo_data=b"photo",
        photo_mime_type="image/jpeg",
    )
    confirmed_without_photo = AttendanceImport(
        author_tg_id=1,
        status=AttendanceImportStatus.CONFIRMED.value,
        photo_data=None,
        photo_mime_type="image/jpeg",
    )
    draft_with_photo = AttendanceImport(
        author_tg_id=1,
        status=AttendanceImportStatus.DRAFT.value,
        photo_data=b"draft-photo",
        photo_mime_type="image/jpeg",
    )
    sqlite_session.add_all([confirmed_with_photo, confirmed_without_photo, draft_with_photo])
    await sqlite_session.flush()
    sqlite_session.add_all(
        [
            AttendanceDate(import_id=confirmed_with_photo.id, date=date(2026, 9, 25)),
            AttendanceDate(import_id=confirmed_with_photo.id, date=date(2026, 9, 27)),
            AttendanceDate(import_id=confirmed_without_photo.id, date=date(2026, 8, 30)),
            AttendanceDate(import_id=draft_with_photo.id, date=date(2026, 7, 1)),
        ]
    )
    await sqlite_session.flush()

    months = await repository.find_import_photo_months(sqlite_session)
    dates = await repository.find_import_photo_dates_for_month(
        sqlite_session,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    assert months == [(2026, 9)]
    assert dates == [(date(2026, 9, 25), confirmed_with_photo.id), (date(2026, 9, 27), confirmed_with_photo.id)]


@pytest.mark.asyncio
async def test_get_draft_by_id_raises_when_missing_per_adr_008(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    with pytest.raises(AttendanceDraftNotFoundError) as exc_info:
        await repository.get_draft_by_id(db, import_id=999)

    assert exc_info.value.details["import_id"] == 999


@pytest.mark.asyncio
async def test_get_draft_by_id_returns_entity_when_found(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    mock_draft = AttendanceImport(id=10, author_tg_id=456)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = mock_draft
    db.execute.return_value = result_mock

    result = await repository.get_draft_by_id(db, import_id=10)

    assert result.id == 10


@pytest.mark.asyncio
async def test_update_draft_state_executes_and_flushes(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    await repository.update_draft_state(db, import_id=1, draft_state_json='{"status": "ready"}')

    db.execute.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_draft_clears_photo_data_and_flushes(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    await repository.cancel_draft(db, import_id=5)

    db.execute.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_stale_drafts_returns_rowcount(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.rowcount = 3
    db.execute.return_value = result_mock

    count = await repository.expire_stale_drafts(db, older_than=datetime(2026, 9, 20))

    assert count == 3
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_date_processed(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    db.scalar.return_value = 1

    processed = await repository.is_date_processed(db, visit_date=date(2026, 9, 25))

    assert processed is True

    db.scalar.return_value = 0
    assert await repository.is_date_processed(db, visit_date=date(2026, 9, 26)) is False


@pytest.mark.asyncio
async def test_save_confirmed_import(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    nested_cm = MagicMock()
    nested_cm.__aenter__ = AsyncMock()
    nested_cm.__aexit__ = AsyncMock()
    db.begin_nested = MagicMock(return_value=nested_cm)

    date_result_mock = MagicMock()
    date_result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = date_result_mock

    confirmed_dates = [
        (
            date(2026, 9, 25),
            [
                {
                    "user_id": 100,
                    "start_time": time(18, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 120,
                    "is_empty_time": False,
                    "source": "photo_ai",
                }
            ],
        )
    ]

    await repository.save_confirmed_import(
        db=db,
        import_id=1,
        confirmed_by_tg_id=777,
        confirmed_dates=confirmed_dates,
    )

    # Added AttendanceDate + AttendanceRecord
    assert db.add.call_count == 2
    assert db.flush.call_count == 3


@pytest.mark.asyncio
async def test_save_confirmed_import_skips_dates_without_valid_records(
    repository: AttendanceRepository,
) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    nested_cm = MagicMock()
    nested_cm.__aenter__ = AsyncMock()
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_cm)

    date_result_mock = MagicMock()
    date_result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = date_result_mock
    db.flush.side_effect = [
        None,
        IntegrityError(
            'duplicate key value violates unique constraint "uq_attendance_records_user_date"',
            params=None,
            orig=Exception("uq_attendance_records_user_date"),
        ),
        None,
        None,
    ]

    confirmed_dates = [
        (
            date(2026, 9, 25),
            [
                {
                    "user_id": 100,
                    "start_time": time(18, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 120,
                    "is_empty_time": False,
                    "source": "photo_ai",
                }
            ],
        )
    ]

    saved = await repository.save_confirmed_import(
        db=db,
        import_id=1,
        confirmed_by_tg_id=777,
        confirmed_dates=confirmed_dates,
    )

    # Newly created AttendanceDate is deleted when all rows fail IntegrityError
    db.delete.assert_awaited_once()
    assert len(saved) == 0


@pytest.mark.asyncio
async def test_save_confirmed_import_atomic_duplicate_handling_real_db(
    repository: AttendanceRepository,
    sqlite_session: AsyncSession,
) -> None:
    db = sqlite_session

    # 1. Create draft and users U and V
    user_u = User(first_name="UserU", telegram_id=1001)
    user_v = User(first_name="UserV", telegram_id=1002)
    draft = AttendanceImport(
        author_tg_id=999,
        status=AttendanceImportStatus.DRAFT.value,
        photo_data=b"test_image",
        photo_mime_type="image/jpeg",
        photo_file_size=10,
        photo_checksum="test_sum",
        raw_ai_json="{}",
        draft_state_json="{}",
    )
    db.add_all([user_u, user_v, draft])
    await db.flush()

    visit_date = date(2026, 9, 25)

    # 2. Insert AttendanceRecord(user_id=U, date=D) directly into DB
    initial_date = AttendanceDate(import_id=draft.id, date=visit_date)
    db.add(initial_date)
    await db.flush()

    initial_record = AttendanceRecord(
        import_id=draft.id,
        attendance_date_id=initial_date.id,
        user_id=user_u.id,
        date=visit_date,
        duration_minutes=60,
        source="manual_admin",
    )
    db.add(initial_record)
    await db.flush()

    # 3. Call save_confirmed_import with two rows on date D: U (duplicate) and V (new)
    confirmed_dates = [
        (
            visit_date,
            [
                {
                    "user_id": user_u.id,
                    "start_time": time(18, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 120,
                    "is_empty_time": False,
                    "source": "photo_ai",
                },
                {
                    "user_id": user_v.id,
                    "start_time": time(18, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 120,
                    "is_empty_time": False,
                    "source": "photo_ai",
                },
            ],
        )
    ]

    saved_result = await repository.save_confirmed_import(
        db=db,
        import_id=draft.id,
        confirmed_by_tg_id=999,
        confirmed_dates=confirmed_dates,
    )

    # 4. Verify IntegrityError did not leak, U was skipped via savepoint, V was saved
    assert len(saved_result) == 1
    result_date, result_rows = saved_result[0]
    assert result_date == visit_date
    assert len(result_rows) == 1
    assert result_rows[0]["user_id"] == user_v.id

    records = (await db.execute(select(AttendanceRecord).where(AttendanceRecord.date == visit_date))).scalars().all()
    assert len(records) == 2
    records_by_uid = {r.user_id: r for r in records}
    assert records_by_uid[user_u.id].duration_minutes == 60
    assert records_by_uid[user_v.id].duration_minutes == 120

    # 5. Scenario 2: if ALL rows on a date conflict on IntegrityError, AttendanceDate is NOT created
    conflict_date = date(2026, 9, 26)
    db.add(
        AttendanceRecord(
            import_id=draft.id,
            attendance_date_id=None,
            user_id=user_u.id,
            date=conflict_date,
            duration_minutes=45,
            source="manual_admin",
        )
    )
    await db.flush()

    all_conflict_dates = [
        (
            conflict_date,
            [
                {
                    "user_id": user_u.id,
                    "start_time": time(19, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 60,
                    "is_empty_time": False,
                    "source": "photo_ai",
                }
            ],
        )
    ]

    all_conflict_result = await repository.save_confirmed_import(
        db=db,
        import_id=draft.id,
        confirmed_by_tg_id=999,
        confirmed_dates=all_conflict_dates,
    )

    assert len(all_conflict_result) == 0

    ad_check = (
        await db.execute(select(AttendanceDate).where(AttendanceDate.date == conflict_date))
    ).scalar_one_or_none()
    assert ad_check is None


@pytest.mark.asyncio
async def test_save_confirmed_import_reraises_non_duplicate_integrity_error(
    repository: AttendanceRepository,
    sqlite_session: AsyncSession,
) -> None:
    db = sqlite_session

    draft = AttendanceImport(
        author_tg_id=999,
        status=AttendanceImportStatus.DRAFT.value,
        photo_data=b"test_image",
        photo_mime_type="image/jpeg",
        photo_file_size=10,
        photo_checksum="test_sum",
        raw_ai_json="{}",
        draft_state_json="{}",
    )
    db.add(draft)
    await db.flush()

    visit_date = date(2026, 9, 25)
    # user_id is None, triggering NOT NULL constraint violation (non-duplicate IntegrityError)
    confirmed_dates = [
        (
            visit_date,
            [
                {
                    "user_id": None,
                    "start_time": time(18, 0),
                    "end_time": time(20, 0),
                    "duration_minutes": 120,
                    "is_empty_time": False,
                    "source": "photo_ai",
                }
            ],
        )
    ]

    with pytest.raises(IntegrityError):
        await repository.save_confirmed_import(
            db=db,
            import_id=draft.id,
            confirmed_by_tg_id=999,
            confirmed_dates=confirmed_dates,
        )


def test_is_attendance_record_duplicate_error_patterns() -> None:
    # Postgres constraint name
    pg_exc = IntegrityError(
        'duplicate key value violates unique constraint "uq_attendance_records_user_date"',
        params=None,
        orig=Exception("uq_attendance_records_user_date"),
    )
    assert _is_attendance_record_duplicate_error(pg_exc) is True

    # Postgres orig attribute constraint_name
    class MockOrig(Exception):
        constraint_name = "uq_attendance_records_user_date"

    pg_attr_exc = IntegrityError("duplicate", params=None, orig=MockOrig("duplicate"))
    assert _is_attendance_record_duplicate_error(pg_attr_exc) is True

    # SQLite duplicate constraint pattern
    sqlite_exc = IntegrityError(
        "(sqlite3.IntegrityError) UNIQUE constraint failed: attendance_records.user_id, attendance_records.date",
        params=None,
        orig=Exception("UNIQUE constraint failed: attendance_records.user_id, attendance_records.date"),
    )
    assert _is_attendance_record_duplicate_error(sqlite_exc) is True

    # SQLite NOT NULL violation -> False
    not_null_exc = IntegrityError(
        "(sqlite3.IntegrityError) NOT NULL constraint failed: attendance_records.user_id",
        params=None,
        orig=Exception("NOT NULL constraint failed: attendance_records.user_id"),
    )
    assert _is_attendance_record_duplicate_error(not_null_exc) is False

    # SQLite FOREIGN KEY violation -> False
    fk_exc = IntegrityError(
        "(sqlite3.IntegrityError) FOREIGN KEY constraint failed",
        params=None,
        orig=Exception("FOREIGN KEY constraint failed"),
    )
    assert _is_attendance_record_duplicate_error(fk_exc) is False

    # Arbitrary / generic error -> False
    generic_exc = IntegrityError("other error", params=None, orig=Exception("other error"))
    assert _is_attendance_record_duplicate_error(generic_exc) is False


@pytest.mark.asyncio
async def test_find_user_attendance_in_month(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    mock_record = AttendanceRecord(id=1, user_id=10, duration_minutes=90)
    result_mock.scalars.return_value.all.return_value = [mock_record]
    db.execute.return_value = result_mock

    records = await repository.find_user_attendance_in_month(
        db=db,
        user_id=10,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    assert len(records) == 1
    assert records[0].id == 1


@pytest.mark.asyncio
async def test_get_user_monthly_summary(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    # 3 participants: uid 10 (300 min, 3 visits), uid 20 (200 min, 2 visits), uid 30 (100 min, 1 visit)
    result_mock.all.return_value = [
        (10, 300, 3),
        (20, 200, 2),
        (30, 100, 1),
    ]
    db.execute.return_value = result_mock

    total_min, rank, total_part, visits = await repository.get_user_monthly_summary(
        db=db,
        user_id=20,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    assert total_min == 200
    assert rank == 2
    assert total_part == 3
    assert visits == 2

    # User not in list (0 attendance)
    total_min, rank, total_part, visits = await repository.get_user_monthly_summary(
        db=db,
        user_id=999,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )
    assert total_min == 0
    assert rank == 0
    assert total_part == 3
    assert visits == 0


@pytest.mark.asyncio
async def test_get_monthly_rankings(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    mock_user = User(id=1, first_name="Иван")
    result_mock.all.return_value = [(mock_user, 120, 2)]
    db.execute.return_value = result_mock

    rankings = await repository.get_monthly_rankings(
        db=db,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    assert len(rankings) == 1
    user, total_min, visits = rankings[0]
    assert user.id == 1
    assert total_min == 120
    assert visits == 2


@pytest.mark.asyncio
async def test_find_import_photo(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.first.return_value = (b"photo_bytes_content", "image/png")
    db.execute.return_value = result_mock

    photo_info = await repository.find_import_photo(db, import_id=1)

    assert photo_info == (b"photo_bytes_content", "image/png")

    result_mock.first.return_value = None
    assert await repository.find_import_photo(db, import_id=2) is None


@pytest.mark.asyncio
async def test_has_user_visit_in_import(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    db.scalar.return_value = 1
    assert await repository.has_user_visit_in_import(db, user_id=1, import_id=10) is True

    db.scalar.return_value = 0
    assert await repository.has_user_visit_in_import(db, user_id=2, import_id=10) is False


@pytest.mark.asyncio
async def test_get_import_dates(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [date(2026, 9, 20), date(2026, 9, 21)]
    db.execute.return_value = result_mock

    dates = await repository.get_import_dates(db, import_id=10)


@pytest.mark.asyncio
async def test_find_user_visit_months(repository: AttendanceRepository) -> None:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.all.return_value = [(2026, 9), (2026, 6)]
    db.execute.return_value = result_mock

    months = await repository.find_user_visit_months(db, user_id=1)
    assert months == [(2026, 9), (2026, 6)]
