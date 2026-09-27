"""Application service for photo attendance accounting and draft orchestration."""

from __future__ import annotations

import calendar
import hashlib
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import AppSettings
from ..db.models import AttendanceImport, AttendanceImportStatus, User
from ..domain.exceptions import (
    AttendanceDomainError,
    AttendanceDraftExpiredError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
    AttendanceUserNotFoundError,
)
from ..domain.services import AttendanceRankingCalculator, ParticipantAttendanceStat
from ..dto import (
    AttendanceDraftDateBlockDTO,
    AttendanceDraftDTO,
    AttendanceDraftItemDTO,
    AttendanceMonthlyRankingItemDTO,
    AttendanceMonthlyReportDTO,
    AttendanceRecognizedDateBlockDTO,
    AttendanceRecognizedRowDTO,
    AttendanceRecordReadDTO,
    AttendanceUserSummaryDTO,
    calculate_duration_minutes,
    parse_time_range,
)
from ..infrastructure import AttendanceRepository, UserRepository
from ..presentation.texts.attendance import MONTH_NAMES_RU
from .ports import AttendanceRecognitionPort

DRAFT_TTL_HOURS: int = 24
_MIN_CONFIDENCE_THRESHOLD: float = 0.7


class AttendanceService:
    """Service orchestrating the photo attendance lifecycle and reporting."""

    def __init__(  # noqa: PLR0913
        self,
        db: AsyncSession,
        attendance_repo: AttendanceRepository,
        user_repo: UserRepository,
        recognition_port: AttendanceRecognitionPort,
        settings: AppSettings,
        ranking_calculator: AttendanceRankingCalculator | None = None,
    ) -> None:
        self._db = db
        self._attendance_repo = attendance_repo
        self._user_repo = user_repo
        self._recognition_port = recognition_port
        self._settings = settings
        self._ranking_calculator = ranking_calculator or AttendanceRankingCalculator()

    # ============ PUBLIC API (NEWSPAPER RULE: HIGH-LEVEL INTENT) ============

    async def ingest_attendance_photo(
        self,
        author_tg_id: int,
        photo_bytes: bytes,
        mime_type: str,
    ) -> AttendanceDraftDTO:
        """Ingest a photo of an attendance sheet, recognize it, and create a draft.

        Args:
            author_tg_id: Telegram ID of author submitting the photo.
            photo_bytes: Raw photo bytes.
            mime_type: Photo MIME type.

        Returns:
            AttendanceDraftDTO: Newly created draft ready for review.

        Raises:
            AttendancePhotoEmptyError: If photo bytes are empty.
            AttendanceRecognitionError: If recognition fails or photo quality is unacceptable.
        """
        if not photo_bytes:
            raise AttendancePhotoEmptyError("Файл фотографии пуст")

        cutoff = datetime.now(UTC) - timedelta(hours=DRAFT_TTL_HOURS)
        await self._attendance_repo.expire_stale_drafts(self._db, older_than=cutoff)
        await self._db.commit()
        checksum = hashlib.sha256(photo_bytes).hexdigest()
        file_size = len(photo_bytes)

        users = await self._user_repo.find_all_users(self._db)
        candidate_names = [self._format_user_full_name(u) for u in users if self._format_user_full_name(u)]

        result = await self._recognition_port.recognize_attendance_sheet(
            photo_bytes=photo_bytes,
            mime_type=mime_type,
            candidate_user_names=candidate_names,
        )

        if not result.quality.is_good:
            error_msg = result.quality.comment or "Качество снимка недостаточно для надежного распознавания"
            raise AttendanceRecognitionError(
                error_msg,
                details={"problems": result.quality.problems},
            )

        tz = ZoneInfo(str(self._settings.attendance_timezone))
        now_tz = datetime.now(tz)
        current_year, current_month = now_tz.year, now_tz.month

        today = now_tz.date()
        date_blocks = await self._build_draft_date_blocks(
            dates=result.dates,
            users=users,
            current_year=current_year,
            current_month=current_month,
            today=today,
        )
        initial_draft = AttendanceDraftDTO(
            draft_id=0,
            author_tg_id=author_tg_id,
            created_at=datetime.now(UTC),
            photo_mime_type=mime_type,
            photo_file_size=file_size,
            photo_checksum=checksum,
            dates=date_blocks,
            status=AttendanceImportStatus.DRAFT.value,
        )

        draft_record = await self._attendance_repo.create_draft(
            db=self._db,
            author_tg_id=author_tg_id,
            photo_data=photo_bytes,
            photo_mime_type=mime_type,
            photo_file_size=file_size,
            photo_checksum=checksum,
            raw_ai_json=result.model_dump_json(),
            draft_state_json=initial_draft.model_dump_json(),
        )

        final_draft = initial_draft.model_copy(update={"draft_id": draft_record.id})
        await self._attendance_repo.update_draft_state(
            db=self._db,
            import_id=draft_record.id,
            draft_state_json=final_draft.model_dump_json(),
        )

        await self._db.commit()
        return final_draft

    async def get_draft(self, draft_id: int) -> AttendanceDraftDTO:
        """Retrieve draft state by ID.

        Args:
            draft_id: Primary key of attendance import draft.

        Returns:
            AttendanceDraftDTO: Loaded draft.

        Raises:
            AttendanceDraftNotFoundError: If draft does not exist.
            AttendanceDomainError: If draft state payload is empty.
        """
        draft_record = await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._check_draft_expired(draft_record)
        if not draft_record.draft_state_json:
            raise AttendanceDomainError(f"Черновик с ID {draft_id} не содержит сохраненного состояния")
        return AttendanceDraftDTO.model_validate_json(draft_record.draft_state_json)

    async def update_draft_item_time(
        self,
        draft_id: int,
        item_id: str,
        start_time: time | None,
        end_time: time | None,
        is_empty_time: bool = False,
    ) -> AttendanceDraftDTO:
        """Update time range for an item in the draft, recalculating duration.

        Args:
            draft_id: Draft primary key.
            item_id: Unique item identifier.
            start_time: New start time.
            end_time: New end time.
            is_empty_time: Whether attendance has empty time.

        Returns:
            AttendanceDraftDTO: Updated draft.
        """
        draft_record = await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._check_draft_expired(draft_record)
        if not draft_record.draft_state_json:
            raise AttendanceDomainError(f"Черновик с ID {draft_id} не содержит сохраненного состояния")
        draft = AttendanceDraftDTO.model_validate_json(draft_record.draft_state_json)

        duration_minutes = 0
        if start_time and end_time:
            duration_minutes = calculate_duration_minutes(start_time, end_time)
        found = False
        new_blocks: list[AttendanceDraftDateBlockDTO] = []
        for block in draft.dates:
            new_items: list[AttendanceDraftItemDTO] = []
            for item in block.items:
                if item.item_id == item_id:
                    found = True
                    new_status: Literal["recognized", "needs_manual_review"] = (
                        "recognized"
                        if (item.matched_user_id is not None and duration_minutes > 0)
                        else "needs_manual_review"
                    )
                    updated_item = item.model_copy(
                        update={
                            "start_time": start_time,
                            "end_time": end_time,
                            "duration_minutes": duration_minutes,
                            "is_empty_time": False,
                            "status": new_status,
                        }
                    )
                    new_items.append(updated_item)
                else:
                    new_items.append(item)
            new_blocks.append(block.model_copy(update={"items": new_items}))

        if not found:
            raise AttendanceDomainError(f"Строка с ID '{item_id}' не найдена в черновике")

        updated_draft = draft.model_copy(update={"dates": new_blocks})
        await self._attendance_repo.update_draft_state(
            db=self._db,
            import_id=draft_id,
            draft_state_json=updated_draft.model_dump_json(),
        )
        await self._db.commit()
        return updated_draft

    async def update_draft_item_participant(
        self,
        draft_id: int,
        item_id: str,
        user_id: int,
    ) -> AttendanceDraftDTO:
        """Bind an item to a resolved participant user ID.

        Args:
            draft_id: Draft primary key.
            item_id: Unique item identifier.
            user_id: User primary key to bind to.

        Returns:
            AttendanceDraftDTO: Updated draft.
        """
        draft_record = await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._check_draft_expired(draft_record)
        if not draft_record.draft_state_json:
            raise AttendanceDomainError(f"Черновик с ID {draft_id} не содержит сохраненного состояния")
        draft = AttendanceDraftDTO.model_validate_json(draft_record.draft_state_json)
        user = await self._user_repo.find_user_by_id(self._db, user_id)
        if user is None:
            raise AttendanceUserNotFoundError(f"Пользователь с ID {user_id} не найден")

        matched_name = self._format_user_full_name(user)

        found = False
        new_blocks: list[AttendanceDraftDateBlockDTO] = []
        for block in draft.dates:
            new_items: list[AttendanceDraftItemDTO] = []
            for item in block.items:
                if item.item_id == item_id:
                    found = True
                    new_status: Literal["recognized", "needs_manual_review"] = (
                        "recognized" if (item.duration_minutes > 0) else "needs_manual_review"
                    )
                    updated_item = item.model_copy(
                        update={
                            "matched_user_id": user_id,
                            "matched_user_name": matched_name,
                            "status": new_status,
                        }
                    )
                    new_items.append(updated_item)
                else:
                    new_items.append(item)
            new_blocks.append(block.model_copy(update={"items": new_items}))

        if not found:
            raise AttendanceDomainError(f"Строка с ID '{item_id}' не найдена в черновике")

        updated_draft = draft.model_copy(update={"dates": new_blocks})
        await self._attendance_repo.update_draft_state(
            db=self._db,
            import_id=draft_id,
            draft_state_json=updated_draft.model_dump_json(),
        )
        await self._db.commit()
        return updated_draft

    async def exclude_draft_item(self, draft_id: int, item_id: str) -> AttendanceDraftDTO:
        """Mark a draft item as excluded from the final import.

        Args:
            draft_id: Draft primary key.
            item_id: Item identifier.

        Returns:
            AttendanceDraftDTO: Updated draft.
        """
        draft_record = await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._check_draft_expired(draft_record)
        if not draft_record.draft_state_json:
            raise AttendanceDomainError(f"Черновик с ID {draft_id} не содержит сохраненного состояния")
        draft = AttendanceDraftDTO.model_validate_json(draft_record.draft_state_json)
        found = False
        new_blocks: list[AttendanceDraftDateBlockDTO] = []
        for block in draft.dates:
            new_items: list[AttendanceDraftItemDTO] = []
            for item in block.items:
                if item.item_id == item_id:
                    found = True
                    new_items.append(item.model_copy(update={"is_excluded": True}))
                else:
                    new_items.append(item)
            new_blocks.append(block.model_copy(update={"items": new_items}))

        if not found:
            raise AttendanceDomainError(f"Строка с ID '{item_id}' не найдена в черновике")

        updated_draft = draft.model_copy(update={"dates": new_blocks})
        await self._attendance_repo.update_draft_state(
            db=self._db,
            import_id=draft_id,
            draft_state_json=updated_draft.model_dump_json(),
        )
        await self._db.commit()
        return updated_draft

    async def cancel_draft(self, draft_id: int) -> None:
        """Cancel a draft and clear its photo data to save storage.

        Args:
            draft_id: Draft primary key.
        """
        await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._attendance_repo.cancel_draft(self._db, draft_id)
        await self._db.commit()

    async def confirm_draft(
        self,
        draft_id: int,
        admin_tg_id: int,
    ) -> tuple[int, int, int]:
        """Confirm the draft and persist confirmed attendance dates and records.

        Filters out excluded items and items without matched users.
        Deduplicates records per user per date by choosing the entry with highest duration.
        Skips dates that are ignored or already processed.

        Args:
            draft_id: Draft primary key.
            admin_tg_id: Telegram ID of administrator confirming import.

        Returns:
            tuple[int, int, int]: (added_dates_count, added_records_count, skipped_dates_count).

        Raises:
            AttendanceDomainError: If draft is not in DRAFT status.
        """
        draft_record = await self._attendance_repo.get_draft_by_id(self._db, draft_id)
        await self._check_draft_expired(draft_record)
        if draft_record.status != AttendanceImportStatus.DRAFT.value:
            raise AttendanceDomainError("Черновик уже обработан или отменен")

        draft = AttendanceDraftDTO.model_validate_json(draft_record.draft_state_json or "{}")

        self._assert_no_unresolved_rows(draft)

        added_dates_count = 0
        added_records_count = 0
        skipped_dates_count = 0

        confirmed_dates: list[tuple[date, list[dict[str, Any]]]] = []
        for block in draft.dates:
            if block.is_ignored:
                skipped_dates_count += 1
                continue

            if await self._attendance_repo.is_date_processed(self._db, block.date):
                skipped_dates_count += 1
                continue

            rows = self._collect_confirmed_user_rows(block.items)
            if not rows:
                skipped_dates_count += 1
                continue

            confirmed_dates.append((block.date, rows))
            added_dates_count += 1
            added_records_count += len(rows)

        if not confirmed_dates:
            raise AttendanceDomainError(
                "Не найдено записей для сохранения: все даты уже обработаны либо все строки исключены."
            )

        await self._attendance_repo.save_confirmed_import(
            db=self._db,
            import_id=draft_id,
            confirmed_by_tg_id=admin_tg_id,
            confirmed_dates=confirmed_dates,
        )

        await self._db.commit()
        return added_dates_count, added_records_count, skipped_dates_count

    async def cleanup_stale_drafts(self) -> int:
        """Expire all stale drafts older than TTL and clear their photo bytes.

        Returns:
            int: Number of expired drafts.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=DRAFT_TTL_HOURS)
        count = await self._attendance_repo.expire_stale_drafts(self._db, older_than=cutoff)
        await self._db.commit()
        return count

    async def get_monthly_rankings(
        self,
        year: int | None = None,
        month: int | None = None,
    ) -> list[AttendanceMonthlyRankingItemDTO]:
        """Get monthly rankings of active participants using competition ranking (1, 2, 2, 4).

        Args:
            year: Optional calendar year. Defaults to current year in configured timezone.
            month: Optional calendar month (1..12). Defaults to current month.

        Returns:
            list[AttendanceMonthlyRankingItemDTO]: Ordered monthly rankings for active participants.
        """
        _, _, start_date, end_date = self._get_month_bounds(year, month)
        raw_rankings = await self._attendance_repo.get_monthly_rankings(self._db, start_date, end_date)
        stats = [
            ParticipantAttendanceStat(
                user_id=u.id,
                user_full_name=self._format_user_full_name(u),
                total_minutes=total_min,
                visits_count=visits,
            )
            for u, total_min, visits in raw_rankings
        ]
        ranked = self._ranking_calculator.calculate_rankings(stats)
        return [
            AttendanceMonthlyRankingItemDTO(
                rank=r.rank,
                user_id=r.user_id,
                user_full_name=r.user_full_name,
                total_minutes=r.total_minutes,
                visits_count=r.visits_count,
            )
            for r in ranked
        ]

    async def get_user_monthly_summary(
        self,
        user_id: int,
        year: int | None = None,
        month: int | None = None,
    ) -> AttendanceUserSummaryDTO:
        """Get summary metrics for user in the given month.

        Args:
            user_id: User primary key.
            year: Optional calendar year. Defaults to current year in configured timezone.
            month: Optional calendar month (1..12). Defaults to current month.

        Returns:
            AttendanceUserSummaryDTO: Aggregated user metrics.
        """
        user = await self._user_repo.find_user_by_id(self._db, user_id)
        if user is None:
            raise AttendanceUserNotFoundError(f"Пользователь с ID {user_id} не найден")

        full_name = self._format_user_full_name(user)
        rankings = await self.get_monthly_rankings(year, month)
        total_participants = len(rankings)

        user_stat = next((item for item in rankings if item.user_id == user_id), None)
        if user_stat is not None:
            total_min = user_stat.total_minutes
            rank = user_stat.rank
            visits = user_stat.visits_count
        else:
            total_min = 0
            rank = 0
            visits = 0

        return AttendanceUserSummaryDTO(
            user_id=user_id,
            user_full_name=full_name,
            total_minutes=total_min,
            rank=rank,
            total_participants=total_participants,
            visits_count=visits,
        )

    async def get_user_monthly_visits(
        self,
        user_id: int,
        year: int | None = None,
        month: int | None = None,
    ) -> list[AttendanceRecordReadDTO]:
        """Get chronological list of attendance records for user in the given month.

        Args:
            user_id: User primary key.
            year: Optional calendar year.
            month: Optional calendar month.

        Returns:
            list[AttendanceRecordReadDTO]: Chronological visit records.
        """
        _, _, start_date, end_date = self._get_month_bounds(year, month)
        records = await self._attendance_repo.find_user_attendance_in_month(
            db=self._db,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

        return [
            AttendanceRecordReadDTO(
                id=r.id,
                user_id=r.user_id,
                user_full_name=self._format_user_full_name(r.user),
                date=r.date,
                start_time=r.start_time,
                end_time=r.end_time,
                duration_minutes=r.duration_minutes,
                is_empty_time=r.is_empty_time,
                source=r.source,
                confirmed_at=r.created_at,
            )
            for r in records
        ]

    async def get_monthly_report(
        self,
        year: int | None = None,
        month: int | None = None,
        detailed: bool = False,
    ) -> AttendanceMonthlyReportDTO:
        """Generate monthly attendance report with rankings and optional records.

        Args:
            year: Calendar year.
            month: Calendar month (1..12).
            detailed: Whether to include individual visit records.

        Returns:
            AttendanceMonthlyReportDTO: Aggregated report.
        """
        calc_year, calc_month, start_date, end_date = self._get_month_bounds(year, month)
        rankings = await self.get_monthly_rankings(year, month)

        total_club_minutes = sum(r.total_minutes for r in rankings)
        active_participants_count = len(rankings)
        avg_minutes = total_club_minutes // active_participants_count if active_participants_count > 0 else 0
        records: list[AttendanceRecordReadDTO] = []
        if detailed:
            records_raw = await self._attendance_repo.get_monthly_records(self._db, start_date, end_date)
            records = [
                AttendanceRecordReadDTO(
                    id=r.id,
                    user_id=r.user_id,
                    user_full_name=self._format_user_full_name(r.user),
                    date=r.date,
                    start_time=r.start_time,
                    end_time=r.end_time,
                    duration_minutes=r.duration_minutes,
                    is_empty_time=r.is_empty_time,
                    source=r.source,
                    confirmed_at=r.created_at,
                )
                for r in records_raw
            ]

        return AttendanceMonthlyReportDTO(
            year=calc_year,
            month=calc_month,
            total_club_minutes=total_club_minutes,
            active_participants_count=active_participants_count,
            average_minutes_per_participant=avg_minutes,
            rankings=rankings,
            records=records,
        )

    async def get_photo_evidence(self, import_id: int) -> tuple[bytes, str]:
        """Retrieve original photo bytes and MIME type for an import.

        Args:
            import_id: Primary key of attendance import.

        Returns:
            tuple[bytes, str]: (photo_bytes, mime_type).

        Raises:
            AttendanceDomainError: If photo evidence was cleared or not found.
        """
        photo_info = await self._attendance_repo.find_import_photo(self._db, import_id)
        if photo_info is None:
            raise AttendanceDomainError(
                "Фото-доказательство не найдено",
                details={"import_id": import_id},
            )
        return photo_info

    # ============ PRIVATE HELPERS (LOWER-LEVEL DETAILS) ============

    async def _check_draft_expired(self, draft_record: AttendanceImport) -> None:
        """Check if draft is expired and expire stale drafts if TTL exceeded."""
        cutoff = datetime.now(UTC) - timedelta(hours=DRAFT_TTL_HOURS)
        if draft_record.status == AttendanceImportStatus.EXPIRED.value:
            raise AttendanceDraftExpiredError("Срок действия черновика истёк")
        if draft_record.created_at:
            created_at = draft_record.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at < cutoff:
                await self._attendance_repo.expire_stale_drafts(self._db, older_than=cutoff)
                await self._db.commit()
                draft_record.status = AttendanceImportStatus.EXPIRED.value
                draft_record.photo_data = None
                raise AttendanceDraftExpiredError(
                    "Срок действия черновика истёк. Черновик переведён в статус EXPIRED, фото удалено."
                )

    def _match_participant(
        self,
        raw_name: str | None,
        users: Sequence[User],
    ) -> tuple[int | None, str | None, Literal["recognized", "needs_manual_review"]]:
        """Match a recognized name against known users.

        Returns (matched_user_id, matched_user_name, status).
        Only unambiguous exact matches across name permutations return status='recognized'.
        """
        if not raw_name or not raw_name.strip():
            return None, None, "needs_manual_review"

        normalized_target = self._normalize_name(raw_name)
        if not normalized_target:
            return None, None, "needs_manual_review"

        matched_users: list[User] = []
        for user in users:
            first = self._normalize_name(user.first_name)
            last = self._normalize_name(user.last_name or "")
            pat = self._normalize_name(user.patronymic or "")

            variants = {
                f"{last} {first} {pat}".strip(),
                f"{last} {first}".strip(),
                f"{first} {last} {pat}".strip(),
                f"{first} {last}".strip(),
            }
            if normalized_target in variants:
                matched_users.append(user)

        if len(matched_users) == 1:
            matched_user = matched_users[0]
            full_name = self._format_user_full_name(matched_user)
            return matched_user.id, full_name, "recognized"

        # Ambiguous match or no match at all
        return None, None, "needs_manual_review"

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize name by lowercasing, replacing yo with e, and collapsing spaces."""
        cleaned = name.lower().replace("ё", "е")
        cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _format_user_full_name(user: User) -> str:
        """Format full name as 'Фамилия Имя Отчество'."""
        parts = [user.last_name, user.first_name, user.patronymic]
        return " ".join(p.strip() for p in parts if p and p.strip())

    def _get_month_bounds(
        self,
        year: int | None,
        month: int | None,
    ) -> tuple[int, int, date, date]:
        """Calculate year, month, and first/last date of the month."""
        if year is None or month is None:
            tz = ZoneInfo(str(self._settings.attendance_timezone))
            now_tz = datetime.now(tz)
            year = year or now_tz.year
            month = month or now_tz.month

        days_in_month = calendar.monthrange(year, month)[1]
        start_date = date(year, month, 1)
        end_date = date(year, month, days_in_month)
        return year, month, start_date, end_date

    @staticmethod
    def _format_period_warning(past_dates: list[date], current_year: int, current_month: int) -> str | None:
        """Format warning message for attendance dates in past calendar months."""
        if not past_dates:
            return None

        cur_month_name = MONTH_NAMES_RU.get(current_month, "")
        d1 = past_dates[0]
        m_name = MONTH_NAMES_RU.get(d1.month, "")
        d1_fmt = d1.strftime("%d.%m.%Y")

        if len(past_dates) == 1:
            return (
                f"Дата на фото: {d1_fmt} — это {m_name} {d1.year}, не текущий месяц. "
                f"Посещения попадут в отчёт за {m_name} {d1.year} "
                f"и не изменят отчёт за {cur_month_name} {current_year}."
            )

        d2 = past_dates[1]
        d2_fmt = d2.strftime("%d.%m.%Y")
        return (
            f"Даты на фото: {d1_fmt} и {d2_fmt} — это {m_name} {d1.year}, не текущий месяц. "
            f"Посещения попадут в отчёт за {m_name} {d1.year} "
            f"и не изменят отчёт за {cur_month_name} {current_year}."
        )

    async def _build_draft_date_blocks(
        self,
        dates: list[AttendanceRecognizedDateBlockDTO],
        users: Sequence[User],
        current_year: int,
        current_month: int,
        today: date,
    ) -> list[AttendanceDraftDateBlockDTO]:
        """Convert recognized date blocks into draft date blocks with date validation and warnings."""
        past_dates_to_warn: list[date] = []
        for date_block in dates:
            if date_block.date <= today:
                processed = await self._attendance_repo.find_processed_date(self._db, date_block.date)
                if processed is None and (date_block.date.year, date_block.date.month) != (current_year, current_month):
                    past_dates_to_warn.append(date_block.date)

        period_warning = self._format_period_warning(past_dates_to_warn, current_year, current_month)

        date_blocks: list[AttendanceDraftDateBlockDTO] = []
        for date_block in dates:
            is_future = date_block.date > today
            is_past_month = (date_block.date.year, date_block.date.month) != (current_year, current_month)

            processed_record = await self._attendance_repo.find_processed_date(self._db, date_block.date)
            is_already_processed = processed_record is not None
            processed_import_id = processed_record.import_id if processed_record else None

            is_ignored = False
            ignore_reason: str | None = None

            if is_future:
                is_ignored = True
                ignore_reason = "будущая дата"
                items: list[AttendanceDraftItemDTO] = []
            elif is_already_processed:
                is_ignored = True
                ignore_reason = "дата уже обработана"
                items = []
            else:
                items = []
                for idx, row in enumerate(date_block.rows):
                    draft_item = self._build_draft_item(
                        row=row,
                        date_val=date_block.date,
                        row_idx=idx,
                        is_already_processed=False,
                        is_out_of_month=is_past_month,
                        users=users,
                    )
                    if draft_item is not None:
                        items.append(draft_item)

            date_blocks.append(
                AttendanceDraftDateBlockDTO(
                    date=date_block.date,
                    is_ignored=is_ignored,
                    ignore_reason=ignore_reason,
                    processed_import_id=processed_import_id,
                    period_warning=period_warning if is_past_month and not is_ignored else None,
                    items=items,
                )
            )

        return date_blocks

    def _build_draft_item(  # noqa: PLR0913
        self,
        row: AttendanceRecognizedRowDTO,
        date_val: date,
        row_idx: int,
        is_already_processed: bool,
        is_out_of_month: bool,
        users: Sequence[User],
    ) -> AttendanceDraftItemDTO | None:
        """Process one recognized row and build a draft item DTO.

        Returns None if row represents an absent participant (empty time).
        """
        if row.is_empty_time:
            return None
        if not row.time_raw or row.time_raw.strip() in ("", "-", "—", "–", "нет", "прочерк", "н", "n", "0"):
            return None

        start_time, end_time, duration_minutes = self._resolve_row_time(
            time_raw=row.time_raw,
            start_time=row.start_time,
            end_time=row.end_time,
            is_empty_time=False,
        )

        matched_user_id, matched_user_name, match_status = self._match_participant(row.raw_name, users)
        if (
            row.status == "needs_manual_review"
            or row.confidence < _MIN_CONFIDENCE_THRESHOLD
            or match_status != "recognized"
            or duration_minutes <= 0
        ):
            status: Literal["recognized", "needs_manual_review"] = "needs_manual_review"
        else:
            status = "recognized"

        item_id = f"{date_val.isoformat()}_{row_idx}_{uuid.uuid4().hex[:6]}"
        return AttendanceDraftItemDTO(
            item_id=item_id,
            date=date_val,
            raw_name=row.raw_name,
            matched_user_id=matched_user_id,
            matched_user_name=matched_user_name,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            is_empty_time=False,
            confidence=row.confidence,
            status=status,
            is_duplicate_date=is_already_processed,
            is_out_of_month=is_out_of_month,
            is_excluded=False,
        )

    @staticmethod
    def _resolve_row_time(
        time_raw: str | None,
        start_time: time | None,
        end_time: time | None,
        is_empty_time: bool,
    ) -> tuple[time | None, time | None, int]:
        """Compute start, end, and duration minutes safely without trusting AI duration."""
        if is_empty_time:
            return None, None, 0
        if time_raw:
            parsed = parse_time_range(time_raw)
            if parsed is not None:
                return parsed
            if start_time and end_time:
                duration = calculate_duration_minutes(start_time, end_time)
                return start_time, end_time, duration
        elif start_time and end_time:
            duration = calculate_duration_minutes(start_time, end_time)
            return start_time, end_time, duration
        return None, None, 0

    @staticmethod
    def _collect_confirmed_user_rows(items: list[AttendanceDraftItemDTO]) -> list[dict[str, Any]]:
        """Collect deduplicated confirmed rows for a single date block, retaining max duration."""
        user_records: dict[int, dict[str, Any]] = {}
        for item in items:
            if item.is_excluded or item.matched_user_id is None or item.duration_minutes <= 0:
                continue
            uid = item.matched_user_id
            if uid not in user_records:
                user_records[uid] = {
                    "user_id": uid,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "duration_minutes": item.duration_minutes,
                    "is_empty_time": False,
                    "source": "photo_ai",
                }
            elif item.duration_minutes > user_records[uid]["duration_minutes"]:
                user_records[uid]["duration_minutes"] = item.duration_minutes
                user_records[uid]["start_time"] = item.start_time
                user_records[uid]["end_time"] = item.end_time
                user_records[uid]["is_empty_time"] = False
        return list(user_records.values())

    @staticmethod
    def _assert_no_unresolved_rows(draft: AttendanceDraftDTO) -> None:
        """Scan all non-ignored date blocks and non-excluded items for unresolved participant rows."""
        for block in draft.dates:
            if block.is_ignored:
                continue
            for item in block.items:
                if item.is_excluded:
                    continue
                if item.matched_user_id is None or item.status == "needs_manual_review":
                    raise AttendanceDomainError(
                        "Нельзя подтвердить импорт: в журнале есть неразрешённые строки. "
                        "Сопоставьте участников или исключите эти строки перед подтверждением."
                    )
