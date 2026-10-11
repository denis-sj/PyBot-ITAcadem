"""Read existing use cases and expose only necessary evidence to the model."""

from datetime import timedelta

from ...core.constants import PointsTypeEnum
from ...dto import UserReadDTO
from ...dto.agent_read import AgentMonth
from ...dto.attendance import AttendanceMonthlyRankingItemDTO, AttendanceTopDTO
from ..attendance_service import AttendanceService
from ..competence import CompetenceService
from ..leaderboard import LeaderboardService
from ..user_services.user_competence import UserCompetenceService
from ..user_services.user_profile import UserProfileService
from ..user_services.user_roles import UserRolesService


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
    ) -> None:
        self.profiles = profiles
        self.attendance = attendance
        self.leaderboard = leaderboard
        self.roles = roles
        self.user_competences = user_competences
        self.competences = competences

    async def get_profile(self, user: UserReadDTO) -> str:
        """Return the caller's profile and the already calculated level progress."""
        profile = await self.profiles.build_profile_view(user)
        name = " ".join(part for part in (user.last_name, user.first_name, user.patronymic) if part)
        return "\n".join(
            (
                f"Мой профиль: {name}; дата регистрации: {user.join_date.isoformat()}",
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

    @staticmethod
    def _duration(minutes: int) -> str:
        hours, remaining = divmod(minutes, 60)
        return f"{minutes} минут ({hours} ч {remaining} мин)"
