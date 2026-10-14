"""Build and deliver the weekly pulse report without calling a generative model."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.config import YEKATERINBURG_TZ
from ..db.models import User
from ..domain.services.community_achievements import calculate_attendance_achievements
from ..domain.services.community_checkins import CheckinStatus, get_reported_checkin_week_start
from ..dto import NotifyDTO
from ..dto.community_checkins import (
    CheckinParticipantDTO,
    PulseAchievementDTO,
    PulseReportDTO,
    PulseReportRecipientDTO,
    WeeklyCheckinReportRowDTO,
)
from ..infrastructure.attendance_repository import AttendanceRepository
from ..infrastructure.community_achievement_repository import CommunityAchievementRepository
from ..infrastructure.pulse_report_renderer import render_pulse_report_pdf
from ..infrastructure.weekly_checkin_repository import WeeklyCheckinRepository
from ..presentation.texts.community_achievements import achievement_title
from ..presentation.texts.community_checkins import PULSE_PDF_CAPTION, PULSE_PDF_FAILED, render_pulse_summary
from ..services.ports import (
    NotificationBlockedError,
    NotificationPermanentError,
    NotificationPort,
    NotificationTemporaryError,
)
from ..services.ports.pulse_report_delivery import PulseReportDeliveryPort
from ..services.technology_stack import TechnologyStackService

_ACHIEVEMENT_ICONS = {
    "first_step": "👣",
    "streak_4": "🔥",
    "streak_8": "🔥",
    "streak_16": "🔥",
    "visits_10": "🔟",
    "visits_25": "🏅",
    "visits_50": "🏆",
    "comeback": "🔄",
    "top_week": "🥇",
    "goal_done": "🎯",
    "mentor": "🎓",
}


class PulseReportService:
    """Compute fixed report metrics, render one PDF, and DM all available admins."""

    def __init__(  # noqa: PLR0913
        self,
        db: AsyncSession,
        checkins: WeeklyCheckinRepository,
        attendance: AttendanceRepository,
        achievements: CommunityAchievementRepository,
        notifications: NotificationPort,
        document_delivery: PulseReportDeliveryPort,
        technology_stack: TechnologyStackService,
    ) -> None:
        self._db = db
        self._checkins = checkins
        self._attendance = attendance
        self._achievements = achievements
        self._notifications = notifications
        self._document_delivery = document_delivery
        self._technology_stack = technology_stack

    async def build_report(self, *, now: datetime | None = None) -> PulseReportDTO:
        """Assemble the completed campaign report from attendance and summary rows."""
        local_now = (now or datetime.now(YEKATERINBURG_TZ)).astimezone(YEKATERINBURG_TZ)
        week_start = get_reported_checkin_week_start(local_now.date())
        week_end = week_start + timedelta(days=6)
        previous_start = week_start - timedelta(days=7)
        previous_end = week_start - timedelta(days=1)
        rows = await self._checkins.find_campaign_rows(self._db, week_start=week_start)
        previous_rows = await self._checkins.find_campaign_rows(self._db, week_start=previous_start)
        answered_rows = tuple(row for row in rows if row.answered_at is not None)
        previous_answered_rows = tuple(row for row in previous_rows if row.answered_at is not None)
        needs_help_rows = tuple(
            row for row in answered_rows if row.status in {CheckinStatus.STUCK, CheckinStatus.NO_MOTIVATION}
        )
        previous_help_count = sum(
            row.status in {CheckinStatus.STUCK, CheckinStatus.NO_MOTIVATION} for row in previous_answered_rows
        )
        goal_count = sum(row.status == CheckinStatus.GOAL_ACHIEVED for row in answered_rows)
        previous_goal_count = sum(row.status == CheckinStatus.GOAL_ACHIEVED for row in previous_answered_rows)

        user_ids = tuple(row.user_id for row in rows)
        all_visit_dates = await self._attendance.find_visit_dates_for_users(self._db, user_ids)
        visit_counts = await self._attendance.find_visit_counts_for_period(
            self._db,
            start_date=week_start,
            end_date=week_end,
            user_ids=user_ids,
        )
        current_visit_count = await self._attendance.count_visits_for_period(
            self._db,
            start_date=week_start,
            end_date=week_end,
        )
        previous_visit_count = await self._attendance.count_visits_for_period(
            self._db,
            start_date=previous_start,
            end_date=previous_end,
        )

        answered_participants = await self._make_participants(
            answered_rows,
            all_visit_dates=all_visit_dates,
            visit_counts=visit_counts,
            as_of=local_now.date(),
            help_rows=frozenset(row.id for row in needs_help_rows),
        )
        non_responders = await self._make_participants(
            tuple(row for row in rows if row.answered_at is None),
            all_visit_dates=all_visit_dates,
            visit_counts=visit_counts,
            as_of=local_now.date(),
            help_rows=frozenset(),
        )
        needs_help = tuple(
            item for item in answered_participants if item.status in {CheckinStatus.STUCK, CheckinStatus.NO_MOTIVATION}
        )

        achievement_start = datetime.combine(week_start, time.min, tzinfo=YEKATERINBURG_TZ)
        achievement_end = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=YEKATERINBURG_TZ)
        achievement_rows = await self._achievements.find_awards_in_period(
            self._db,
            start_at=achievement_start,
            end_at=achievement_end,
        )
        names_by_code: dict[str, list[str]] = defaultdict(list)
        for award in achievement_rows:
            full_name = " ".join(part for part in (award.first_name, award.last_name) if part).strip()
            names_by_code[award.code].append(full_name or "Участник")
        pulse_achievements = tuple(
            PulseAchievementDTO(
                code=code,
                title=achievement_title(code),
                icon=_ACHIEVEMENT_ICONS.get(code, "🏅"),
                participant_names=tuple(names),
            )
            for code, names in names_by_code.items()
        )

        return PulseReportDTO(
            week_start=week_start,
            week_end=week_end,
            invited_count=len(rows),
            answered_count=len(answered_rows),
            needs_help_count=len(needs_help_rows),
            goals_achieved_count=goal_count,
            visit_count=current_visit_count,
            answered_delta=len(answered_rows) - len(previous_answered_rows),
            needs_help_delta=len(needs_help_rows) - previous_help_count,
            goals_achieved_delta=goal_count - previous_goal_count,
            visits_delta=current_visit_count - previous_visit_count,
            needs_help=needs_help,
            answered_participants=answered_participants,
            achievements=pulse_achievements,
            non_responders=non_responders,
            learning_technologies=tuple(await self._technology_stack.get_top_learning_technologies(5)),
        )

    async def deliver_weekly_report(self, *, now: datetime | None = None) -> int:
        """Send a PDF report to every available Admin, falling back to the same KPI text."""
        report = await self.build_report(now=now)
        recipients = await self._checkins.find_report_recipients(self._db)
        if not recipients:
            return 0

        pdf = self._render_report(report)
        caption = self._build_caption(report)
        fallback = self._build_fallback(report)
        delivered = 0
        for recipient in recipients:
            delivered += await self._deliver_to_admin(recipient, report, pdf, caption, fallback)
        return delivered

    @staticmethod
    def _render_report(report: PulseReportDTO) -> bytes | None:
        """Render once and switch to text delivery if the renderer fails."""
        try:
            return render_pulse_report_pdf(report)
        except Exception:
            logger.exception(
                "event=pulse_report_render status=failed week_start={week_start}", week_start=report.week_start
            )
            return None

    @staticmethod
    def _build_caption(report: PulseReportDTO) -> str:
        """Render the compact Telegram document caption from report KPIs."""
        return PULSE_PDF_CAPTION.format(
            answered=report.answered_count,
            invited=report.invited_count,
            needs_help=report.needs_help_count,
        )

    @staticmethod
    def _build_fallback(report: PulseReportDTO) -> str:
        """Render the documented plain-text fallback with all four KPIs."""
        return PULSE_PDF_FAILED + "\n" + render_pulse_summary(report)

    async def _deliver_to_admin(
        self,
        recipient: PulseReportRecipientDTO,
        report: PulseReportDTO,
        pdf: bytes | None,
        caption: str,
        fallback: str,
    ) -> int:
        """Deliver PDF or fallback while translating blocked and permanent failures."""
        if pdf is None:
            return await self._send_fallback(recipient, fallback)
        try:
            await self._document_delivery.send_pdf(
                recipient_id=recipient.telegram_id,
                filename=f"pulse-{report.week_start.isoformat()}.pdf",
                content=pdf,
                caption=caption,
            )
        except NotificationBlockedError:
            await self._mark_user_blocked(recipient.user_id)
            return 0
        except NotificationTemporaryError:
            raise
        except NotificationPermanentError as error:
            logger.warning(
                "event=pulse_report_delivery status=failed user_id={user_id} error={error}",
                user_id=recipient.user_id,
                error=str(error),
            )
            return await self._send_fallback(recipient, fallback)
        return 1

    async def _send_fallback(self, recipient: PulseReportRecipientDTO, fallback: str) -> int:
        """Deliver the text report fallback when PDF rendering or upload fails."""
        try:
            await self._notifications.send_message(NotifyDTO(recipient_id=recipient.telegram_id, message=fallback))
        except NotificationBlockedError:
            await self._mark_user_blocked(recipient.user_id)
            return 0
        except NotificationTemporaryError:
            raise
        except NotificationPermanentError as error:
            logger.warning(
                "event=pulse_report_fallback status=failed user_id={user_id} error={error}",
                user_id=recipient.user_id,
                error=str(error),
            )
            return 0
        return 1

    async def _make_participants(
        self,
        rows: tuple[WeeklyCheckinReportRowDTO, ...],
        *,
        all_visit_dates: dict[int, list[date]],
        visit_counts: dict[int, int],
        as_of: date,
        help_rows: frozenset[int],
    ) -> tuple[CheckinParticipantDTO, ...]:
        participants: list[CheckinParticipantDTO] = []
        for row in rows:
            visit_dates = all_visit_dates.get(row.user_id, [])
            dates_before_report = [visit_date for visit_date in visit_dates if visit_date <= as_of]
            last_visit = dates_before_report[-1] if dates_before_report else None
            current_streak = calculate_attendance_achievements(dates_before_report, today=as_of).current_streak
            silent_weeks = max(0, (as_of - last_visit).days // 7) if last_visit else 99
            helper_names: tuple[str, ...] = ()
            if row.id in help_rows and row.status == CheckinStatus.STUCK and row.help_topic:
                recommendation = await self._technology_stack.find_helpers(row.user_id, row.help_topic)
                helper_names = tuple(
                    f"{'@' + helper.telegram_username if helper.telegram_username else helper.full_name}"
                    f"{' · ментор' if helper.is_mentor else ''}"
                    for helper in recommendation.helpers
                )
            participants.append(
                CheckinParticipantDTO(
                    user_id=row.user_id,
                    first_name=row.first_name,
                    last_name=row.last_name,
                    telegram_username=row.telegram_username,
                    goal_text=row.goal_text,
                    summary=row.summary,
                    status=row.status,
                    visit_count=visit_counts.get(row.user_id, 0),
                    current_streak=current_streak,
                    last_visit=last_visit,
                    silent_weeks=silent_weeks,
                    helper_names=helper_names,
                )
            )
        return tuple(participants)

    async def _mark_user_blocked(self, user_id: int) -> None:
        user = await self._db.scalar(select(User).where(User.id == user_id))
        if user is not None:
            user.bot_is_blocked = True
            await self._db.commit()
