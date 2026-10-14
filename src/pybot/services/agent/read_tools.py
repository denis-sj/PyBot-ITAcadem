"""Read tools have no model-controlled recipient or mutation argument."""

from pydantic_ai import RunContext

from ...dto.agent_read import AgentMonth
from ...dto.technology_stack import TechnologyName
from ...presentation.texts.texts import INFO_GLOBAL
from .dependencies import AgentDependencies
from .reads import format_user_candidates

ADMIN_AGENT_ROLES = frozenset({"Admin"})


async def get_my_profile(ctx: RunContext[AgentDependencies]) -> str:
    """Мой профиль: баллы, уровни, роли и компетенции (/profile)."""
    return await ctx.deps.reads.get_profile(await ctx.deps.get_actor())


async def get_my_achievements(ctx: RunContext[AgentDependencies]) -> str:
    """Мои достижения, значки и текущая серия посещений."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_my_achievements(user.id)


async def get_my_technology_stack(ctx: RunContext[AgentDependencies]) -> str:
    """Мой стек технологий с уровнями «знаю» и «изучаю»."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_my_stack(user.id)


async def find_technology_helpers(
    ctx: RunContext[AgentDependencies],
    technology: TechnologyName,
) -> str:
    """Кто поможет с указанной технологией: сначала подходящие менторы, затем участники."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.find_stack_helpers(user.id, technology)


async def get_my_attendance(ctx: RunContext[AgentDependencies]) -> str:
    """Мои часы, количество посещений и место за текущий месяц (/visits)."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_attendance_summary(user.id)


async def get_my_visits(ctx: RunContext[AgentDependencies], period: AgentMonth | None = None) -> str:
    """История моих посещений по датам и выбранному месяцу.

    Для числа и часов за текущий месяц используй get_my_attendance.
    """
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_visits(user.id, period)


async def get_attendance_week_top(ctx: RunContext[AgentDependencies]) -> str:
    """Топ посещаемости текущей недели и моё место (/visits), не баллы."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_week_top(user.id)


async def get_attendance_month_top(ctx: RunContext[AgentDependencies], period: AgentMonth | None = None) -> str:
    """Топ посещаемости месяца и моё место (/visits). По умолчанию текущий месяц."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_month_top(user.id, period)


async def get_points_leaderboard(ctx: RunContext[AgentDependencies]) -> str:
    """Лидерборд академических и репутационных баллов за прошлую календарную неделю (/leaderboard)."""
    await ctx.deps.get_actor()
    return await ctx.deps.reads.get_points_leaderboard()


async def get_roles_catalog(ctx: RunContext[AgentDependencies]) -> str:
    """Каталог доступных ролей и их описания (/roles)."""
    await ctx.deps.get_actor()
    return await ctx.deps.reads.get_roles_catalog()


async def get_competences_catalog(ctx: RunContext[AgentDependencies]) -> str:
    """Каталог компетенций и их описания (/competences)."""
    await ctx.deps.get_actor()
    return await ctx.deps.reads.get_competences_catalog()


async def get_my_roles(ctx: RunContext[AgentDependencies]) -> str:
    """Мои назначенные роли (/showroles), без доступа к чужим данным."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_user_roles(user.id)


async def get_my_competences(ctx: RunContext[AgentDependencies]) -> str:
    """Мои компетенции (/showcompetences), без доступа к чужим данным."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_user_competences(user.id)


async def get_bot_info(ctx: RunContext[AgentDependencies]) -> str:
    """Информация о боте (/info)."""
    await ctx.deps.get_actor()
    return INFO_GLOBAL


async def get_current_month(ctx: RunContext[AgentDependencies]) -> str:
    """Расчётный месяц учёта посещаемости (/current_month, только Admin)."""
    return ctx.deps.reads.get_current_accounting_month()


async def get_club_attendance_report(ctx: RunContext[AgentDependencies], detailed: bool = False) -> str:
    """Отчёт клуба за текущий месяц (/report, только Admin); detailed добавляет записи посещений."""
    return await ctx.deps.reads.get_club_attendance_report(detailed)


async def get_user_profile(ctx: RunContext[AgentDependencies], telegram_id: int) -> str:
    """Профиль другого участника по точному Telegram ID (только Admin)."""
    return await ctx.deps.reads.get_profile_for_telegram_id(telegram_id)


async def find_user_candidates(ctx: RunContext[AgentDependencies], name_query: str) -> str:
    """Найти зарегистрированных пользователей по ФИО или точному @username."""
    result = await ctx.deps.reads.find_user_candidates(name_query)
    ctx.deps.record_recipient_search(result)
    return format_user_candidates(result)


async def get_user_attendance(
    ctx: RunContext[AgentDependencies], telegram_id: int, period: AgentMonth | None = None
) -> str:
    """Посещаемость участника по Telegram ID и месяцу (только Admin); без месяца берётся текущий."""
    return await ctx.deps.reads.get_attendance_for_telegram_id(telegram_id, period or AgentMonth.current())


async def get_current_chat_id(ctx: RunContext[AgentDependencies]) -> str:
    """ID текущего чата и топика (/chat_id, только Admin)."""
    conversation = ctx.deps.conversation
    text = f"ID чата: {conversation.chat_id}."
    if conversation.topic_id is not None:
        text += f" ID топика: {conversation.topic_id}."
    return text
