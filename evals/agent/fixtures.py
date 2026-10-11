"""Synthetic read results; no database, Telegram or production services."""

from pybot.dto import UserReadDTO
from pybot.dto.agent_read import AgentMonth
from pybot.services.agent.reads import AgentReadService


class EvalReads(AgentReadService):
    """Replace every read use case with deterministic, non-personal evidence."""

    def __init__(self) -> None:
        pass

    async def get_profile(self, user: UserReadDTO) -> str:
        return "Мой профиль: Тест; академические баллы: 75; репутационные баллы: 12; роль Student."

    async def get_attendance_summary(self, user_id: int) -> str:
        return "Мои посещения текущего месяца: 90 минут (1 ч 30 мин); посещений: 2; место: 3."

    async def get_visits(self, user_id: int, period: AgentMonth | None = None) -> str:
        if period is None:
            return "Уточните месяц посещений. Доступные месяцы: 2020-09, 2020-08."
        if period == AgentMonth(year=2020, month=9):
            return "Мои посещения за 2020-09: 2; итого 90 минут. Даты: 2020-09-07, 2020-09-09."
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
