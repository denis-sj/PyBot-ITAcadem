"""Synthetic read results; no database, Telegram or production services."""

from pybot.dto import UserReadDTO
from pybot.dto.agent_read import AgentMonth, AgentUserCandidate, AgentUserSearchResult
from pybot.services.agent.reads import AgentReadService


class EvalReads(AgentReadService):
    """Replace every read use case with deterministic, non-personal evidence."""

    def __init__(self, search_mode: str = "unique") -> None:
        self.search_mode = search_mode
        self.allowed_search_recipient_ids: set[int] = set()

    async def get_profile(self, user: UserReadDTO) -> str:
        return "Мой профиль: Тест; академические баллы: 75; репутационные баллы: 12; роль Student."

    async def get_my_achievements(self, user_id: int) -> str:
        return "🔥 Текущая серия посещений: 3 недели\n🏅 Достижения: Постоянство · 4 недели, Десятка"

    async def get_my_goal(self, user_id: int) -> str:
        del user_id
        return "Моя активная цель: «подготовиться к собеседованию на Python»."

    async def get_my_stack(self, user_id: int) -> str:
        del user_id
        return "Твой стек:\nЗнаю: Python, SQL.\nИзучаю: Docker."

    async def find_stack_helpers(self, user_id: int, technology: str) -> str:
        del user_id, technology
        return "Кстати, в Python у нас разбираются: @mentor_python (ментор), Иван Петров. Можешь написать им."

    async def get_upcoming_meetings(self, user_id: int) -> str:
        del user_id
        return "Ближайших встреч для тебя пока нет."

    async def get_meeting_attendees(self, meeting_id: int, user_id: int) -> str:
        del meeting_id, user_id
        return "На встречу отметились: Тест Иванов."

    async def get_profile_for_telegram_id(self, telegram_id: int) -> str:
        return "Профиль участника: Тест; академические баллы: 75; репутационные баллы: 12; роль Student."

    async def find_user_candidates(self, name_query: str) -> AgentUserSearchResult:
        candidates = [
            AgentUserCandidate(
                full_name="Иванов Иван Иванович",
                telegram_id=771234,
                telegram_username="test_student",
            )
        ]
        if "Петров" in name_query:
            candidates = [
                AgentUserCandidate(
                    full_name="Петрова Анна Сергеевна",
                    telegram_id=884422,
                    telegram_username="anna_test",
                )
            ]
        if self.search_mode == "ambiguous":
            candidates.append(
                AgentUserCandidate(
                    full_name="Иванов Иван Петрович",
                    telegram_id=884422,
                    telegram_username="ivan_test",
                )
            )
        elif self.search_mode == "none":
            candidates = []

        self.allowed_search_recipient_ids = (
            {candidate.telegram_id for candidate in candidates} if len(candidates) == 1 else set()
        )
        return AgentUserSearchResult(
            name_query=name_query,
            candidates=candidates,
            total_matches=len(candidates),
        )

    async def get_attendance_for_telegram_id(self, telegram_id: int, period: AgentMonth) -> str:
        return f"Посещаемость участника за {period.year}-{period.month:02}: 90 минут; посещений: 2; место: 3."

    @staticmethod
    def get_current_accounting_month() -> str:
        return "Текущий расчётный месяц учёта: октябрь 2026; номер месяца: 10."

    async def get_club_attendance_report(self, detailed: bool = False) -> str:
        if detailed:
            return "Отчёт клуба за октябрь 2026: всего 90 минут; Тест — 90 минут; 2026-10-07, 18:00–19:30."
        return "Отчёт клуба за октябрь 2026: всего 90 минут; активных участников: 1."

    async def get_attendance_summary(self, user_id: int) -> str:
        return "Мои посещения текущего месяца: 90 минут (1 ч 30 мин); посещений: 2; место: 3."

    async def get_visits(self, user_id: int, period: AgentMonth | None = None) -> str:
        if period is None:
            return "Уточните месяц посещений. Доступные месяцы: 2020-09, 2020-08."
        if period == AgentMonth(year=2020, month=9):
            return "Мои посещения за 2020-09: 2; итого 90 минут. Даты: 2020-09-07, 2020-09-09."
        if period == AgentMonth(year=2026, month=9):
            return "Мои посещения за 2026-09: 0; записей нет."
        return "За выбранный месяц посещений нет. Доступные месяцы: 2020-09, 2020-08."

    async def get_week_top(self, user_id: int) -> str:
        return "Топ посещаемости текущей недели: место 1 — Тест, 90 минут; посещений: 2."

    async def get_month_top(self, user_id: int, period: AgentMonth | None = None) -> str:
        return "Топ посещаемости месяца: место 1 — Тест, 90 минут; посещений: 2."

    async def get_points_leaderboard(self) -> str:
        return "Лидерборд баллов прошлой недели: академические — Тест, 25; репутационные — Тест, 5."

    async def get_roles_catalog(self) -> str:
        return "Student: участник клуба. Admin: администратор клуба."

    async def get_competences_catalog(self) -> str:
        return "Python: язык программирования. SQL: работа с базами данных."

    async def get_user_roles(self, user_id: int) -> str:
        return "Мои роли: Student."

    async def get_user_competences(self, user_id: int) -> str:
        return "Мои компетенции: не назначены."
