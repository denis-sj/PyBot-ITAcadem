"""Repository for attendance imports, dates, and records persistence."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, cast, extract, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import (
    AttendanceDate,
    AttendanceImport,
    AttendanceImportStatus,
    AttendanceRecord,
    User,
)
from ..domain.exceptions import AttendanceDraftNotFoundError


class AttendanceRepository:
    """Stateless repository for managing attendance imports and records."""

    async def create_draft(  # noqa: PLR0913
        self,
        db: AsyncSession,
        author_tg_id: int,
        photo_data: bytes,
        photo_mime_type: str,
        photo_file_size: int,
        photo_checksum: str,
        raw_ai_json: str,
        draft_state_json: str,
    ) -> AttendanceImport:
        """Create a new attendance import record in DRAFT status.

        Args:
            db: Database session.
            author_tg_id: Telegram ID of author uploading the sheet.
            photo_data: Raw image bytes.
            photo_mime_type: Image MIME type.
            photo_file_size: Image size in bytes.
            photo_checksum: SHA256 checksum of image.
            raw_ai_json: Raw JSON payload returned from AI.
            draft_state_json: Initial JSON state of the draft DTO.

        Returns:
            AttendanceImport: Persisted draft instance.
        """
        draft = AttendanceImport(
            author_tg_id=author_tg_id,
            status=AttendanceImportStatus.DRAFT.value,
            photo_data=photo_data,
            photo_mime_type=photo_mime_type,
            photo_file_size=photo_file_size,
            photo_checksum=photo_checksum,
            raw_ai_json=raw_ai_json,
            draft_state_json=draft_state_json,
        )
        db.add(draft)
        await db.flush()
        return draft

    async def find_draft_by_id(
        self,
        db: AsyncSession,
        import_id: int,
    ) -> AttendanceImport | None:
        """Find an attendance import by its ID, or return None if not found.

        Args:
            db: Database session.
            import_id: Primary key of the import record.

        Returns:
            AttendanceImport or None.
        """
        stmt = select(AttendanceImport).where(AttendanceImport.id == import_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_draft_by_id(
        self,
        db: AsyncSession,
        import_id: int,
    ) -> AttendanceImport:
        """Get an attendance import by its ID, raising AttendanceDraftNotFoundError if not found.

        Args:
            db: Database session.
            import_id: Primary key of the import record.

        Returns:
            AttendanceImport instance.

        Raises:
            AttendanceDraftNotFoundError: If import record does not exist.
        """
        draft = await self.find_draft_by_id(db, import_id)
        if draft is None:
            raise AttendanceDraftNotFoundError(
                f"Attendance draft with ID {import_id} was not found",
                import_id=import_id,
            )
        return draft

    async def update_draft_state(
        self,
        db: AsyncSession,
        import_id: int,
        draft_state_json: str,
    ) -> None:
        """Update draft_state_json for the specified import.

        Args:
            db: Database session.
            import_id: Primary key of the import record.
            draft_state_json: Serialized draft JSON state.
        """
        stmt = (
            update(AttendanceImport).where(AttendanceImport.id == import_id).values(draft_state_json=draft_state_json)
        )
        await db.execute(stmt)
        await db.flush()

    async def cancel_draft(
        self,
        db: AsyncSession,
        import_id: int,
    ) -> None:
        """Mark draft as CANCELLED and clear stored photo bytes and draft state to save disk space.

        Args:
            db: Database session.
            import_id: Primary key of the import record.
        """
        stmt = (
            update(AttendanceImport)
            .where(AttendanceImport.id == import_id)
            .values(
                status=AttendanceImportStatus.CANCELLED.value,
                photo_data=None,
                draft_state_json=None,
            )
        )
        await db.execute(stmt)
        await db.flush()

    async def has_user_visit_in_import(
        self,
        db: AsyncSession,
        user_id: int,
        import_id: int,
    ) -> bool:
        """Check if user has at least one confirmed attendance record in the import.

        Args:
            db: Database session.
            user_id: User primary key.
            import_id: Import primary key.

        Returns:
            bool: True if record exists, False otherwise.
        """
        stmt = (
            select(func.count())
            .select_from(AttendanceRecord)
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.import_id == import_id,
            )
        )
        count = await db.scalar(stmt)
        return bool(count and count > 0)

    async def get_import_dates(
        self,
        db: AsyncSession,
        import_id: int,
    ) -> list[dt.date]:
        """Get all dates associated with the given import ordered chronologically.

        Args:
            db: Database session.
            import_id: Import primary key.

        Returns:
            list[dt.date]: Sorted list of unique calendar dates.
        """
        stmt = (
            select(AttendanceDate.date).where(AttendanceDate.import_id == import_id).order_by(AttendanceDate.date.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def expire_stale_drafts(
        self,
        db: AsyncSession,
        older_than: dt.datetime,
    ) -> int:
        """Mark DRAFT imports created before older_than as EXPIRED and clear photo_data.

        Args:
            db: Database session.
            older_than: Cutoff timestamp.

        Returns:
            int: Number of updated records.
        """
        stmt = (
            update(AttendanceImport)
            .where(
                AttendanceImport.status == AttendanceImportStatus.DRAFT.value,
                AttendanceImport.created_at < older_than,
            )
            .values(
                status=AttendanceImportStatus.EXPIRED.value,
                photo_data=None,
            )
        )
        result = await db.execute(stmt)
        await db.flush()
        rowcount = getattr(result, "rowcount", 0)
        return int(rowcount) if rowcount is not None else 0

    async def find_processed_date(
        self,
        db: AsyncSession,
        visit_date: dt.date,
    ) -> AttendanceDate | None:
        """Find persisted AttendanceDate record for the given calendar date if already processed.

        Args:
            db: Database session.
            visit_date: Calendar date to lookup.

        Returns:
            AttendanceDate | None: Persisted AttendanceDate model if found, None otherwise.
        """
        stmt = select(AttendanceDate).where(AttendanceDate.date == visit_date)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def is_date_processed(
        self,
        db: AsyncSession,
        visit_date: dt.date,
    ) -> bool:
        """Check whether attendance for the given date has already been processed.

        Args:
            db: Database session.
            visit_date: Date to check.

        Returns:
            bool: True if date is already in attendance_dates.
        """
        stmt = select(func.count()).select_from(AttendanceDate).where(AttendanceDate.date == visit_date)
        count = await db.scalar(stmt)
        return bool(count and count > 0)

    async def save_confirmed_import(
        self,
        db: AsyncSession,
        import_id: int,
        confirmed_by_tg_id: int,
        confirmed_dates: list[tuple[dt.date, list[dict[str, Any]]]],
    ) -> None:
        """Persist confirmed import with its dates and records.

        - Updates AttendanceImport: status=CONFIRMED, confirmed_at=func.now(), confirmed_by_tg_id.
          Retains photo_data.
        - Inserts AttendanceDate for each non-duplicate date.
        - Inserts AttendanceRecord for each row under the date.

        Args:
            db: Database session.
            import_id: Primary key of the import record.
            confirmed_by_tg_id: Telegram ID of confirming user.
            confirmed_dates: List of (date, rows_dict_list).
        """
        stmt = (
            update(AttendanceImport)
            .where(AttendanceImport.id == import_id)
            .values(
                status=AttendanceImportStatus.CONFIRMED.value,
                confirmed_at=func.now(),
                confirmed_by_tg_id=confirmed_by_tg_id,
            )
        )
        await db.execute(stmt)

        for visit_date, rows in confirmed_dates:
            date_stmt = select(AttendanceDate).where(AttendanceDate.date == visit_date)
            date_res = await db.execute(date_stmt)
            attendance_date = date_res.scalar_one_or_none()
            if attendance_date is None:
                attendance_date = AttendanceDate(
                    import_id=import_id,
                    date=visit_date,
                )
                db.add(attendance_date)
                await db.flush()

            for row in rows:
                record = AttendanceRecord(
                    import_id=import_id,
                    attendance_date_id=attendance_date.id,
                    user_id=row["user_id"],
                    date=visit_date,
                    start_time=row.get("start_time"),
                    end_time=row.get("end_time"),
                    duration_minutes=row.get("duration_minutes", 0),
                    is_empty_time=row.get("is_empty_time", False),
                    source=row.get("source", "photo_ai"),
                )
                db.add(record)

        await db.flush()

    async def find_user_attendance_in_month(
        self,
        db: AsyncSession,
        user_id: int,
        start_date: dt.date,
        end_date: dt.date,
    ) -> Sequence[AttendanceRecord]:
        """Find attendance records for user within [start_date, end_date] with user eagerly loaded.

        Args:
            db: Database session.
            user_id: User primary key.
            start_date: Start of range (inclusive).
            end_date: End of range (inclusive).

        Returns:
            Sequence of AttendanceRecord objects.
        """
        stmt = (
            select(AttendanceRecord)
            .options(selectinload(AttendanceRecord.user))
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.duration_minutes > 0,
                AttendanceRecord.date >= start_date,
                AttendanceRecord.date <= end_date,
            )
            .order_by(AttendanceRecord.date.asc(), AttendanceRecord.id.asc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def find_user_visit_months(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> list[tuple[int, int]]:
        """Return distinct (year, month) pairs where user has confirmed visits with duration > 0, newest first.

        Args:
            db: Database session.
            user_id: User primary key.

        Returns:
            list[tuple[int, int]]: List of (year, month) tuples ordered by year desc, month desc.
        """
        year_expr = cast(extract("year", AttendanceRecord.date), Integer)
        month_expr = cast(extract("month", AttendanceRecord.date), Integer)
        stmt = (
            select(year_expr, month_expr)
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.duration_minutes > 0,
            )
            .distinct()
            .order_by(year_expr.desc(), month_expr.desc())
        )
        result = await db.execute(stmt)
        return [(int(y), int(m)) for y, m in result.all()]

    async def get_user_monthly_summary(
        self,
        db: AsyncSession,
        user_id: int,
        start_date: dt.date,
        end_date: dt.date,
    ) -> tuple[int, int, int, int]:
        """Get summary metrics for user in the given date range.

        Args:
            db: Database session.
            user_id: User primary key.
            start_date: Start date of period.
            end_date: End date of period.

        Returns:
            tuple[int, int, int, int]: (total_minutes, rank, total_participants, visits_count).
        """
        total_minutes_col = func.coalesce(func.sum(AttendanceRecord.duration_minutes), 0).label("total_minutes")
        visits_count_col = func.count(AttendanceRecord.id).label("visits_count")

        stmt = (
            select(
                AttendanceRecord.user_id,
                total_minutes_col,
                visits_count_col,
            )
            .where(
                AttendanceRecord.date >= start_date,
                AttendanceRecord.date <= end_date,
            )
            .group_by(AttendanceRecord.user_id)
            .order_by(total_minutes_col.desc(), AttendanceRecord.user_id.asc())
        )
        result = await db.execute(stmt)
        rankings = result.all()

        total_participants = len(rankings)
        total_minutes = 0
        rank = 0
        visits_count = 0

        for idx, (uid, minutes, visits) in enumerate(rankings, start=1):
            if uid == user_id:
                rank = idx
                total_minutes = int(minutes)
                visits_count = int(visits)
                break

        return (total_minutes, rank, total_participants, visits_count)

    async def get_monthly_rankings(
        self,
        db: AsyncSession,
        start_date: dt.date,
        end_date: dt.date,
    ) -> Sequence[tuple[User, int, int]]:
        """Get monthly rankings of participants ordered by total_minutes desc, user_id asc.

        Args:
            db: Database session.
            start_date: Start date of period.
            end_date: End date of period.

        Returns:
            Sequence of (User, total_minutes, visits_count).
        """
        total_minutes_col = func.coalesce(func.sum(AttendanceRecord.duration_minutes), 0).label("total_minutes")
        visits_count_col = func.count(AttendanceRecord.id).label("visits_count")

        stmt = (
            select(
                User,
                total_minutes_col,
                visits_count_col,
            )
            .join(AttendanceRecord, AttendanceRecord.user_id == User.id)
            .where(
                AttendanceRecord.date >= start_date,
                AttendanceRecord.date <= end_date,
            )
            .group_by(User.id)
            .order_by(total_minutes_col.desc(), User.id.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()
        return [(user, int(total_min), int(visits)) for user, total_min, visits in rows]

    async def get_monthly_records(
        self,
        db: AsyncSession,
        start_date: dt.date,
        end_date: dt.date,
    ) -> Sequence[AttendanceRecord]:
        """Get all attendance records in the month ordered by date and user_id.

        Args:
            db: Database session.
            start_date: Start date of period.
            end_date: End date of period.

        Returns:
            Sequence of AttendanceRecord objects with loaded user relationship.
        """
        stmt = (
            select(AttendanceRecord)
            .options(selectinload(AttendanceRecord.user))
            .where(
                AttendanceRecord.date >= start_date,
                AttendanceRecord.date <= end_date,
            )
            .order_by(AttendanceRecord.date.asc(), AttendanceRecord.user_id.asc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def find_import_photo(
        self,
        db: AsyncSession,
        import_id: int,
    ) -> tuple[bytes, str] | None:
        """Find photo bytes and mime type for an import, or None if absent.

        Args:
            db: Database session.
            import_id: Primary key of the import record.

        Returns:
            tuple[bytes, str] or None.
        """
        stmt = select(AttendanceImport.photo_data, AttendanceImport.photo_mime_type).where(
            AttendanceImport.id == import_id,
            AttendanceImport.photo_data.isnot(None),
        )
        result = await db.execute(stmt)
        row = result.first()
        if row is None or row[0] is None or row[1] is None:
            return None
        return (row[0], row[1])

    get_import_photo = find_import_photo
