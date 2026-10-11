"""Confirmed role request tool for registered students."""

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from ...domain.exceptions import DomainError
from .dependencies import AgentDependencies


async def validate_mentor_request(ctx: RunContext[AgentDependencies]) -> None:
    """Re-check request eligibility before Pydantic AI asks for approval."""
    actor = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_role_request(actor.id)
    except DomainError as error:
        raise ModelRetry(
            "Заявку на роль Mentor сейчас подать нельзя. Нужна роль Student; заявка недоступна для Mentor и Admin, "
            "при активной заявке или кулдауне."
        ) from error


async def request_mentor_role(ctx: RunContext[AgentDependencies]) -> str:
    """Подать заявку на роль Mentor от имени пользователя после его подтверждения."""
    actor = await ctx.deps.get_actor()
    await ctx.deps.actions.request_mentor_role(actor.id)
    return "Заявка на роль Mentor отправлена администраторам."
