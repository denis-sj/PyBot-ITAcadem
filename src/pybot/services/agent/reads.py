"""Read existing use cases and expose only necessary evidence to the model."""

from datetime import datetime, timedelta

from ...core.constants import YEKATERINBURG_TZ, PointsTypeEnum
from ...dto import ProfileViewDTO, UserReadDTO
from ...dto.agent_read import AgentMonth
from ...dto.attendance import AttendanceMonthlyRankingItemDTO, AttendanceMonthlyReportDTO, AttendanceTopDTO
from ...presentation.texts.attendance import MONTH_NAMES_RU_CAPITALIZED
from ..attendance_service import AttendanceService
from ..competence import CompetenceService
from ..leaderboard import LeaderboardService
from ..user_services.user_competence import UserCompetenceService
from ..user_services.user_profile import UserProfileService
from ..user_services.user_roles import UserRolesService
from ..user_services.users import UserService

_MAX_REPORT_RANKING_ROWS = 25
_MAX_REPORT_DETAIL_ROWS = 30


class AgentReadService:
    """Reuse command services without duplicating rankings or exposing internal IDs."""

    def __init__(  # noqa: PLR0913
        self,
        profiles: UserProfileService,
        attendance: AttendanceService,
        leaderboard: LeaderboardService,
        roles: UserRolesService,
        user_competences: UserCompetenceService,
        competences: CompetenceService,
        users: UserService,
    ) -> None:
        self.profiles = profiles
        self.attendance = attendance
        self.leaderboard = leaderboard
        self.roles = roles
        self.user_competences = user_competences
        self.competences = competences
        self.users = users

    async def get_profile(self, user: UserReadDTO) -> str:
        """Return the caller's profile and the already calculated level progress."""
        profile = await self.profiles.build_profile_view(user)
        return self._format_profile(user, profile, "Мой профиль")

    def _format_profile(self, user: UserReadDTO, profile: ProfileViewDTO, title: str) -> str:
        """Render only profile fields already calculated by the profile use case."""
        name = " ".join(part for part in (user.last_name, user.first_name, user.patronymic) if part)
        return "\n".join(
            (
                f"{title}: {name}; дата регистрации: {user.join_date.isoformat()}",
                f"Академические баллы: {profile.academic_progress.value}; "
                f"уровень: {profile.academic_level.current_level.name}; "
                f"прогресс уровня: {profile.academic_current_points.value} из {profile.academic_next_points.value}",
                f"Репутационные баллы: {profile.reputation_progress.value}; "
                f"уровень: {profile.reputation_level.current_level.name}; "
                f"прогресс уровня: {profile.reputation_current_points.value} из {profile.reputation_next_points.value}",
                "Мои роли: " + (", ".join(profile.roles_data) or "не назначены"),
                "Мои компетенции: " + (", ".join(item.name for item in profile.competences) or "не назначены"),
            )
        )

    async def get_profile_for_telegram_id(self, telegram_id: int) -> str:
        """Return another registered user's profile after the caller's admin check."""
        user = await self.users.find_user_by_telegram_id(telegram_id)
        if user is None:
            return "Пользователь не зарегистрирован."
        profile = await self.profiles.build_profile_view(user)
        return self._format_profile(user, profile, "Профиль участника")

    async def get_attendance_for_telegram_id(self, telegram_id: int, period: AgentMonth) -> str:
        """Read an existing monthly attendance summary for a specific registered user."""
        user = await self.users.find_user_by_telegram_id(telegram_id)
        if user is None:
            return "Пользователь не зарегистрирован."
        summary = await self.attendance.get_user_monthly_summary(user.id, year=period.year, month=period.month)
        return (
            f"Посещаемость {summary.user_full_name} за {period.year}-{period.month:02}: "
            f"{self._duration(summary.total_minutes)}; посещений: {summary.visits_count}; "
            f"место: {summary.rank}; участников: {summary.total_participants}"
        )

    @staticmethod
    def get_current_accounting_month() -> str:
        """Describe the same Yekaterinburg accounting month as /current_month."""
        current = datetime.now(YEKATERINBURG_TZ)
        month_name = MONTH_NAMES_RU_CAPITALIZED.get(current.month, f"Месяц {current.month}")
        return (
            f"Текущий расчётный месяц учёта: {month_name} {current.year}; "
            f"номер месяца: {current.month}; загруженные фото за этот период учитываются в расчёте."
        )

    async def get_club_attendance_report(self, detailed: bool = False) -> str:
        """Read the current club report using the same attendance query as /report."""
        report = await self.attendance.get_monthly_report(detailed=detailed)
        return self._format_attendance_report(report, detailed)

    async def get_attendance_summary(self, user_id: int) -> str:
        """Read the current monthly summary from the attendance service."""
        period = AgentMonth.current()
        summary = await self.attendance.get_user_monthly_summary(user_id, year=period.year, month=period.month)
        return (
            f"Мои посещения за {period.year}-{period.month:02}: "
            f"{self._duration(summary.total_minutes)}; посещений: {summary.visits_count}; "
            f"место: {summary.rank}; участников: {summary.total_participants}"
        )

    async def get_visits(self, user_id: int, period: AgentMonth | None = None) -> str:
        """Follow /visits: ask for a month when several recorded months are available."""
        months = await self.attendance.get_user_visit_months(user_id)
        if not months:
            return "Подтверждённых посещений пока нет."
        available = ", ".join(f"{year}-{month:02}" for year, month in months)
        if period is None:
            if len(months) != 1:
                return f"Уточните месяц посещений. Доступные месяцы: {available}."
            year, month = months[0]
            period = AgentMonth(year=year, month=month)
        if (period.year, period.month) not in months:
            return f"За выбранный месяц посещений нет. Доступные месяцы: {available}."
        summary = await self.attendance.get_user_monthly_summary(user_id, year=period.year, month=period.month)
        visits = await self.attendance.get_user_monthly_visits(user_id, year=period.year, month=period.month)
        lines = [
            f"Мои посещения за {period.year}-{period.month:02}: {summary.visits_count}; "
            f"итого {self._duration(summary.total_minutes)}"
        ]
        for visit in visits:
            interval = (
                f"{visit.start_time:%H:%M}–{visit.end_time:%H:%M}"
                if visit.start_time is not None and visit.end_time is not None
                else "время не указано"
            )
            lines.append(f"{visit.date.isoformat()}: {interval}; {self._duration(visit.duration_minutes)}")
        return "\n".join(lines)

    async def get_week_top(self, user_id: int) -> str:
        """Read the current attendance week ranking with existing tie rules."""
        return self._format_top(await self.attendance.get_week_top(user_id), "Топ посещаемости недели")

    async def get_month_top(self, user_id: int, period: AgentMonth | None = None) -> str:
        """Read a selected month, defaulting to the current business month."""
        period = period or AgentMonth.current()
        top = await self.attendance.get_month_top(user_id, year=period.year, month=period.month)
        return self._format_top(top, "Топ посещаемости месяца")

    async def get_points_leaderboard(self) -> str:
        """Read both point rankings for the previous calendar week, as /leaderboard does."""
        period = self.leaderboard.get_previous_calendar_week_period()
        end_date = period.end.date() - timedelta(days=1)
        lines = [f"Лидерборд баллов за {period.start.date().isoformat()}–{end_date.isoformat()}"]
        for points_type in PointsTypeEnum:
            rows = await self.leaderboard.get_previous_calendar_week_leaderboard(points_type=points_type)
            label = "Академические баллы" if points_type == PointsTypeEnum.ACADEMIC else "Репутационные баллы"
            lines.append(label)
            lines.extend(f"{row.display_name}: {row.total_points_delta}" for row in rows)
            if not rows:
                lines.append("Начислений за период нет.")
        return "\n".join(lines)

    async def get_roles_catalog(self) -> str:
        """Read public role descriptions."""
        roles = await self.roles.find_all_roles()
        return (
            "\n".join(f"{role.name}: {role.description or 'без описания'}" for role in roles) or "Каталог ролей пуст."
        )

    async def get_competences_catalog(self) -> str:
        """Read public competence descriptions."""
        competences = await self.competences.find_all_competencies()
        return (
            "\n".join(f"{item.name}: {item.description or 'без описания'}" for item in competences)
            or "Каталог компетенций пуст."
        )

    async def get_user_roles(self, user_id: int) -> str:
        """Read only the authenticated caller's roles."""
        roles = await self.roles.find_user_roles(user_id)
        return "Мои роли: " + (", ".join(roles) or "не назначены")

    async def get_user_competences(self, user_id: int) -> str:
        """Read only the authenticated caller's competences."""
        competences = await self.user_competences.find_user_competencies(user_id)
        return "Мои компетенции: " + (", ".join(item.name for item in competences) or "не назначены")

    @classmethod
    def _format_top(cls, top: AttendanceTopDTO, label: str) -> str:
        lines = [
            f"{label}: {top.period_start.isoformat()}–{top.period_end.isoformat()}; "
            f"участников: {top.total_participants}"
        ]
        lines.extend(cls._format_ranking(row) for row in top.rows)
        if not top.rows:
            lines.append("Посещений за период нет.")
        lines.append(
            "Моё место: " + cls._format_ranking(top.current_user)
            if top.current_user is not None
            else "Моих посещений за период нет."
        )
        return "\n".join(lines)

    @classmethod
    def _format_ranking(cls, row: AttendanceMonthlyRankingItemDTO) -> str:
        return (
            f"Место {row.rank}: {row.user_full_name}; {cls._duration(row.total_minutes)}; посещений: {row.visits_count}"
        )

    @classmethod
    def _format_attendance_report(cls, report: AttendanceMonthlyReportDTO, detailed: bool) -> str:
        """Bound report output while making omitted rows explicit to the model."""
        month_name = MONTH_NAMES_RU_CAPITALIZED.get(report.month, f"Месяц {report.month}")
        lines = [
            f"Отчёт по посещаемости клуба за {month_name} {report.year}.",
            f"Всего: {cls._duration(report.total_club_minutes)}; "
            f"активных участников: {report.active_participants_count}; "
            f"среднее на участника: {cls._duration(report.average_minutes_per_participant)}.",
            "Рейтинг участников:",
        ]
        if not report.rankings:
            lines.append("За этот месяц посещений нет.")
        else:
            lines.extend(cls._format_ranking(row) for row in report.rankings[:_MAX_REPORT_RANKING_ROWS])
            if len(report.rankings) > _MAX_REPORT_RANKING_ROWS:
                lines.append(
                    f"В рейтинге показаны первые {_MAX_REPORT_RANKING_ROWS} из {len(report.rankings)} участников."
                )
        if detailed and report.records:
            lines.append("Детализация посещений:")
            for index, record in enumerate(report.records[:_MAX_REPORT_DETAIL_ROWS], start=1):
                interval = (
                    f"{record.start_time:%H:%M}–{record.end_time:%H:%M}"
                    if record.start_time is not None and record.end_time is not None
                    else "время не указано"
                )
                lines.append(
                    f"{index}. {record.date.isoformat()} {record.user_full_name}: {interval}; "
                    f"{cls._duration(record.duration_minutes)}"
                )
            if len(report.records) > _MAX_REPORT_DETAIL_ROWS:
                lines.append(
                    f"В детализации показаны первые {_MAX_REPORT_DETAIL_ROWS} из {len(report.records)} посещений."
                )
        elif detailed:
            lines.append("Детализация посещений пуста.")
        return "\n".join(lines)

    @staticmethod
    def _duration(minutes: int) -> str:
        hours, remaining = divmod(minutes, 60)
        return f"{minutes} минут ({hours} ч {remaining} мин)"
