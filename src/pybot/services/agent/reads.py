"""Read existing use cases and expose only necessary evidence to the model."""

import re
from datetime import datetime, timedelta

from ...core.constants import YEKATERINBURG_TZ, PointsTypeEnum
from ...db.models import User
from ...dto import ProfileViewDTO, UserReadDTO
from ...dto.agent_read import AgentMonth, AgentUserCandidate, AgentUserSearchResult
from ...dto.attendance import AttendanceMonthlyRankingItemDTO, AttendanceMonthlyReportDTO, AttendanceTopDTO
from ...presentation.texts.attendance import MONTH_NAMES_RU_CAPITALIZED
from ...presentation.texts.community_achievements import render_achievement_profile_section
from ...presentation.texts.technology_stack import render_helper_recommendation, render_stack_read
from ..attendance_service import AttendanceService
from ..community_achievements import CommunityAchievementService
from ..competence import CompetenceService
from ..leaderboard import LeaderboardService
from ..technology_stack import TechnologyStackService
from ..user_services.user_competence import UserCompetenceService
from ..user_services.user_profile import UserProfileService
from ..user_services.user_roles import UserRolesService
from ..user_services.users import UserService

_MAX_REPORT_RANKING_ROWS = 25
_MAX_REPORT_DETAIL_ROWS = 30
_MAX_USER_CANDIDATES = 5
_USERNAME_QUERY_PATTERN = re.compile(r"[A-Za-z0-9_]{5,32}")


def format_user_candidates(result: AgentUserSearchResult) -> str:
    """Present exact candidate IDs and ask the administrator to resolve ambiguity."""
    if not result.candidates:
        return "Совпадений не нашёл. Уточните полное имя, @username или точный Telegram ID."

    lines = [f"Кандидаты по запросу «{result.name_query}»: "]
    for candidate in result.candidates:
        full_name = candidate.full_name or "Имя не указано"
        username = f" (@{candidate.telegram_username})" if candidate.telegram_username else ""
        lines.append(f"{full_name}{username} — Telegram ID {candidate.telegram_id}")
    if result.total_matches > len(result.candidates):
        lines.append(
            f"Показаны первые {len(result.candidates)} из {result.total_matches} совпадений. "
            "Уточните фамилию или Telegram ID."
        )
    elif result.total_matches > 1:
        lines.append("Уточните Telegram ID нужного участника; не выбирайте за пользователя.")
    return "\n".join(lines)


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
        community_achievements: CommunityAchievementService | None = None,
        technology_stack: TechnologyStackService | None = None,
    ) -> None:
        self.profiles = profiles
        self.attendance = attendance
        self.leaderboard = leaderboard
        self.roles = roles
        self.user_competences = user_competences
        self.competences = competences
        self.users = users
        self.community_achievements = community_achievements
        self.technology_stack = technology_stack

    async def get_my_achievements(self, user_id: int) -> str:
        """Return the caller's current weekly streak and durable achievement history."""
        if self.community_achievements is None:
            return render_achievement_profile_section(0, ())
        summary = await self.community_achievements.get_user_summary(user_id)
        return render_achievement_profile_section(summary.current_streak, summary.achievements)

    async def get_my_stack(self, user_id: int) -> str:
        """Return only the requesting participant's self-reported stack."""
        if self.technology_stack is None:
            return "Стек участников сейчас недоступен."
        return render_stack_read(await self.technology_stack.get_user_stack(user_id))

    async def find_stack_helpers(self, user_id: int, technology: str) -> str:
        """Find unblocked mentors and peers who can help with one technology."""
        if self.technology_stack is None:
            return "Подбор помощников сейчас недоступен."
        recommendation = await self.technology_stack.find_helpers(user_id, technology)
        return render_helper_recommendation(recommendation)

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

    async def find_user_candidates(self, name_query: str) -> AgentUserSearchResult:
        """Find registered users by full name or exact Telegram username."""
        query = name_query.strip()
        if not query:
            return AgentUserSearchResult(name_query=name_query, candidates=[], total_matches=0)

        participants = await self.attendance.get_sorted_participants()
        username_query = self._username_query(query)
        matches: list[AgentUserCandidate] = []
        if username_query is not None:
            normalized_username_query = username_query.casefold()
            for participant in participants:
                username = participant.telegram_username
                if username is None or username.removeprefix("@").casefold() != normalized_username_query:
                    continue
                matches.append(self._user_candidate(participant))
        else:
            search_terms = tuple(term.casefold() for term in query.split() if term)
            for participant in participants:
                full_name = self._full_name(participant.last_name, participant.first_name, participant.patronymic)
                if all(term in full_name.casefold() for term in search_terms):
                    matches.append(self._user_candidate(participant))

        return AgentUserSearchResult(
            name_query=name_query,
            candidates=matches[:_MAX_USER_CANDIDATES],
            total_matches=len(matches),
        )

    @staticmethod
    def _username_query(query: str) -> str | None:
        """Recognize an explicit @username or an unprefixed Telegram username."""
        if query.startswith("@"):
            return query[1:]
        if _USERNAME_QUERY_PATTERN.fullmatch(query) is not None:
            return query
        return None

    @classmethod
    def _user_candidate(cls, user: User) -> AgentUserCandidate:
        """Keep the persisted name, username, and Telegram identity together."""
        return AgentUserCandidate(
            full_name=cls._full_name(user.last_name, user.first_name, user.patronymic),
            telegram_id=user.telegram_id,
            telegram_username=user.telegram_username.removeprefix("@") if user.telegram_username else None,
        )

    @staticmethod
    def _full_name(last_name: str | None, first_name: str | None, patronymic: str | None) -> str:
        return " ".join(part for part in (last_name, first_name, patronymic) if part)

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
            period = period or AgentMonth.current()
            return f"Мои посещения за {period.year}-{period.month:02}: 0; подтверждённых записей нет."
        available = ", ".join(f"{year}-{month:02}" for year, month in months)
        if period is None:
            if len(months) != 1:
                return f"Уточните месяц посещений. Доступные месяцы: {available}."
            year, month = months[0]
            period = AgentMonth(year=year, month=month)
        if (period.year, period.month) not in months:
            return (
                f"Мои посещения за {period.year}-{period.month:02}: 0; подтверждённых записей нет. "
                f"Доступные месяцы: {available}."
            )
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
