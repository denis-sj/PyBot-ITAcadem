"""Read tools have no model-controlled recipient or mutation argument."""

from pydantic_ai import RunContext

from ...dto.agent_read import AgentMonth
from ...presentation.texts.texts import INFO_GLOBAL
from .dependencies import AgentDependencies


async def get_my_profile(ctx: RunContext[AgentDependencies]) -> str:
    """Мой профиль: баллы, уровни, роли и компетенции (/profile)."""
    return await ctx.deps.reads.get_profile(await ctx.deps.get_actor())


async def get_my_attendance(ctx: RunContext[AgentDependencies]) -> str:
    """Мои часы, количество посещений и место за текущий месяц (/visits)."""
    user = await ctx.deps.get_actor()
    return await ctx.deps.reads.get_attendance_summary(user.id)


async def get_my_visits(ctx: RunContext[AgentDependencies], period: AgentMonth | None = None) -> str:
    """Мои посещения по датам (/visits). Без месяца предложит выбор, если месяцев несколько."""
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
