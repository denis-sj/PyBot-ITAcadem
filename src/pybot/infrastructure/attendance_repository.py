"""Repository for attendance imports, dates, and records persistence."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, cast, extract, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core import logger
from ..db.models import (
    AttendanceChangeHistory,
    AttendanceDate,
    AttendanceImport,
    AttendanceImportStatus,
    AttendanceRecord,
    User,
)
from ..domain.exceptions import AttendanceDraftNotFoundError


def _is_attendance_record_duplicate_error(exc: IntegrityError) -> bool:
    """Check if IntegrityError was caused by uq_attendance_records_user_date violation.

    Matches PostgreSQL (constraint name uq_attendance_records_user_date) and
    SQLite (unique constraint failed on attendance_records.user_id, attendance_records.date).
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        constraint_name = getattr(orig, "constraint_name", None)
        if constraint_name == "uq_attendance_records_user_date":
            return True

    text_parts = [str(exc)]
    if orig is not None:
        text_parts.append(str(orig))
    error_text = " ".join(text_parts).lower()

    if "uq_attendance_records_user_date" in error_text:
        return True

    return bool(
        "unique" in error_text
        and (
            "unique constraint failed: attendance_records.user_id, attendance_records.date" in error_text
            or ("attendance_records.user_id" in error_text and "attendance_records.date" in error_text)
            or ("unique constraint failed" in error_text and "user_id" in error_text and "date" in error_text)
        )
    )


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

    async def find_import_photo_months(self, db: AsyncSession) -> list[tuple[int, int]]:
        """Find months with confirmed attendance dates whose original photos remain available.

        Args:
            db: Database session.

        Returns:
            list[tuple[int, int]]: Distinct (year, month) pairs, newest first.
        """
        year_expr = cast(extract("year", AttendanceDate.date), Integer)
        month_expr = cast(extract("month", AttendanceDate.date), Integer)
        stmt = (
            select(year_expr, month_expr)
            .join(AttendanceImport, AttendanceDate.import_id == AttendanceImport.id)
            .where(
                AttendanceImport.status == AttendanceImportStatus.CONFIRMED.value,
                AttendanceImport.photo_data.isnot(None),
            )
            .distinct()
            .order_by(year_expr.desc(), month_expr.desc())
        )
        result = await db.execute(stmt)
        return [(int(year), int(month)) for year, month in result.all()]

    async def find_import_photo_dates_for_month(
        self,
        db: AsyncSession,
        start_date: dt.date,
        end_date: dt.date,
    ) -> list[tuple[dt.date, int]]:
        """Find confirmed dates with available photos in a date range, oldest first.

        Args:
            db: Database session.
            start_date: First included calendar date.
            end_date: Last included calendar date.

        Returns:
            list[tuple[dt.date, int]]: Recorded dates paired with photo numbers.
        """
        stmt = (
            select(AttendanceDate.date, AttendanceDate.import_id)
            .join(AttendanceImport, AttendanceDate.import_id == AttendanceImport.id)
            .where(
                AttendanceDate.date >= start_date,
                AttendanceDate.date <= end_date,
                AttendanceImport.status == AttendanceImportStatus.CONFIRMED.value,
                AttendanceImport.photo_data.isnot(None),
            )
            .order_by(AttendanceDate.date.asc())
        )
        result = await db.execute(stmt)
        return [(visit_date, int(import_id)) for visit_date, import_id in result.all()]

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
    ) -> list[tuple[dt.date, list[dict[str, Any]]]]:
        """Persist confirmed import with its dates and records.

        - Updates AttendanceImport: status=CONFIRMED, confirmed_at=func.now(), confirmed_by_tg_id.
          Retains photo_data.
        - Inserts AttendanceDate ONLY for dates where at least one AttendanceRecord was saved.
        - Inserts AttendanceRecord for each row under the date.

        Args:
            db: Database session.
            import_id: Primary key of the import record.
            confirmed_by_tg_id: Telegram ID of confirming user.
            confirmed_dates: List of (date, rows_dict_list).

        Returns:
            list[tuple[dt.date, list[dict[str, Any]]]]: Dates and records that were actually saved.
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

        actually_saved_dates: list[tuple[dt.date, list[dict[str, Any]]]] = []

        for visit_date, rows in confirmed_dates:
            if not rows:
                continue

            date_stmt = select(AttendanceDate).where(AttendanceDate.date == visit_date)
            date_res = await db.execute(date_stmt)
            attendance_date = date_res.scalar_one_or_none()
            created_new_date = False
            if attendance_date is None:
                attendance_date = AttendanceDate(
                    import_id=import_id,
                    date=visit_date,
                )
                db.add(attendance_date)
                await db.flush()
                created_new_date = True

            actually_saved_rows: list[dict[str, Any]] = []
            for row in rows:
                try:
                    async with db.begin_nested():
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
                        actually_saved_rows.append(row)
                except IntegrityError as exc:
                    if not _is_attendance_record_duplicate_error(exc):
                        raise
                    logger.warning(
                        "Duplicate record detected on insert | user_id={user_id} date={date}",
                        user_id=row["user_id"],
                        date=visit_date,
                    )

            if not actually_saved_rows:
                if created_new_date:
                    await db.delete(attendance_date)
                    await db.flush()
                continue

            actually_saved_dates.append((visit_date, actually_saved_rows))
        await db.flush()
        return actually_saved_dates

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

    async def find_months_with_visits(self, db: AsyncSession) -> list[tuple[int, int]]:
        """Return distinct (year, month) pairs in which anyone has a confirmed visit, newest first.

        Only records with a positive duration are counted, as in the rankings.

        Args:
            db: Database session.

        Returns:
            list[tuple[int, int]]: List of (year, month) tuples ordered by year desc, month desc.
        """
        year_expr = cast(extract("year", AttendanceRecord.date), Integer)
        month_expr = cast(extract("month", AttendanceRecord.date), Integer)
        stmt = (
            select(year_expr, month_expr)
            .where(AttendanceRecord.duration_minutes > 0)
            .distinct()
            .order_by(year_expr.desc(), month_expr.desc())
        )
        result = await db.execute(stmt)
        return [(int(y), int(m)) for y, m in result.all()]

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

    async def find_user_visit_dates(self, db: AsyncSession, user_id: int) -> list[dt.date]:
        """Return all recorded visit dates for a participant in chronological order."""
        statement = (
            select(AttendanceRecord.date).where(AttendanceRecord.user_id == user_id).order_by(AttendanceRecord.date)
        )
        result = await db.execute(statement)
        return list(result.scalars().all())

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

    async def get_rankings_for_period(
        self,
        db: AsyncSession,
        start_date: dt.date,
        end_date: dt.date,
    ) -> Sequence[tuple[User, int, int]]:
        """Get participant totals for an inclusive date range ordered by total_minutes desc, user_id asc.

        Args:
            db: Database session.
            start_date: First included calendar date.
            end_date: Last included calendar date.

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

    async def find_record_by_id(
        self,
        db: AsyncSession,
        record_id: int,
    ) -> AttendanceRecord | None:
        """Find an AttendanceRecord by primary key with user loaded.

        Args:
            db: Database session.
            record_id: Primary key of AttendanceRecord.

        Returns:
            AttendanceRecord | None: Loaded model or None.
        """
        stmt = (
            select(AttendanceRecord)
            .options(selectinload(AttendanceRecord.user))
            .where(AttendanceRecord.id == record_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

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

    async def find_user_record_on_date(
        self,
        db: AsyncSession,
        user_id: int,
        visit_date: dt.date,
    ) -> AttendanceRecord | None:
        """Find existing attendance record for a user on a given date.

        Args:
            db: Database session.
            user_id: User primary key.
            visit_date: Calendar date to check.

        Returns:
            AttendanceRecord | None: Found record or None.
        """
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.user_id == user_id,
            AttendanceRecord.date == visit_date,
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    async def has_user_visit_on_date(
        self,
        db: AsyncSession,
        user_id: int,
        visit_date: dt.date,
    ) -> bool:
        """Check if user already has a visit recorded on the given date.

        Args:
            db: Database session.
            user_id: User primary key.
            visit_date: Calendar date to check.

        Returns:
            bool: True if user has visit on date with duration > 0.
        """
        stmt = (
            select(func.count())
            .select_from(AttendanceRecord)
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.date == visit_date,
                AttendanceRecord.duration_minutes > 0,
            )
        )
        count = await db.scalar(stmt)
        return bool(count and count > 0)

    async def create_manual_record(  # noqa: PLR0913
        self,
        db: AsyncSession,
        user_id: int,
        visit_date: dt.date,
        start_time: dt.time,
        end_time: dt.time,
        duration_minutes: int,
    ) -> AttendanceRecord:
        """Create and persist a manual attendance record."""
        record = AttendanceRecord(
            import_id=None,
            attendance_date_id=None,
            user_id=user_id,
            date=visit_date,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            is_empty_time=False,
            source="manual",
        )
        db.add(record)
        await db.flush()
        return record

    async def update_record_time(
        self,
        db: AsyncSession,
        record_id: int,
        start_time: dt.time,
        end_time: dt.time,
        duration_minutes: int,
    ) -> AttendanceRecord | None:
        """Update start_time, end_time, duration of an AttendanceRecord."""
        record = await self.find_record_by_id(db, record_id)
        if record is None:
            return None
        record.start_time = start_time
        record.end_time = end_time
        record.duration_minutes = duration_minutes
        record.is_empty_time = False
        await db.flush()
        return record

    async def delete_record(
        self,
        db: AsyncSession,
        record_id: int,
    ) -> AttendanceRecord | None:
        """Delete an AttendanceRecord by primary key."""
        record = await self.find_record_by_id(db, record_id)
        if record is None:
            return None
        await db.delete(record)
        await db.flush()
        return record

    async def add_change_history(  # noqa: PLR0913
        self,
        db: AsyncSession,
        user_id: int,
        record_id: int | None,
        visit_date: dt.date,
        action: str,
        old_value: str | None,
        new_value: str | None,
        admin_tg_id: int,
        admin_name: str,
    ) -> AttendanceChangeHistory:
        """Record an entry in the attendance change history audit log."""
        history = AttendanceChangeHistory(
            user_id=user_id,
            record_id=record_id,
            visit_date=visit_date,
            action=action,
            old_value=old_value,
            new_value=new_value,
            admin_tg_id=admin_tg_id,
            admin_name=admin_name,
        )
        db.add(history)
        await db.flush()
        return history

    async def get_recent_change_history(
        self,
        db: AsyncSession,
        user_id: int,
        visit_date: dt.date,
        limit: int = 3,
    ) -> list[AttendanceChangeHistory]:
        """Get most recent change history records for a user on a given date."""
        stmt = (
            select(AttendanceChangeHistory)
            .where(
                AttendanceChangeHistory.user_id == user_id,
                AttendanceChangeHistory.visit_date == visit_date,
            )
            .order_by(AttendanceChangeHistory.created_at.desc(), AttendanceChangeHistory.id.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def find_modified_record_ids(
        self,
        db: AsyncSession,
        record_ids: Sequence[int],
    ) -> set[int]:
        """Find record IDs that have had their time edited by an administrator.

        Args:
            db: Database session.
            record_ids: Sequence of AttendanceRecord primary keys.

        Returns:
            set[int]: Set of record IDs with 'edit_time' action in change history.
        """
        if not record_ids:
            return set()
        stmt = (
            select(AttendanceChangeHistory.record_id)
            .where(
                AttendanceChangeHistory.record_id.in_(record_ids),
                AttendanceChangeHistory.action == "edit_time",
            )
            .distinct()
        )
        result = await db.scalars(stmt)
        return {rid for rid in result if rid is not None}
