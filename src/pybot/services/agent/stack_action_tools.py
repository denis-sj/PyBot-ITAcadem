"""Confirmed actions that modify only the calling participant's technology stack."""

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart

from ...domain.exceptions import DomainError
from ...domain.services.technology_stack import TechnologyLevel, normalize_technology_name
from ...dto.technology_stack import TechnologyName
from .dependencies import AgentDependencies


async def validate_my_technology_update(
    ctx: RunContext[AgentDependencies],
    technology: TechnologyName,
    level: TechnologyLevel,
) -> None:
    """Check the authenticated user's add or level change before approval."""
    user = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_my_stack_item(user.id, technology, level)
    except DomainError as error:
        message = "Не удалось подготовить изменение вашего стека. Проверьте название и лимит в 20 технологий."
        raise ModelRetry(message) from error


async def set_my_technology(
    ctx: RunContext[AgentDependencies],
    technology: TechnologyName,
    level: TechnologyLevel,
) -> str:
    """Add or update one item in the caller's own technology stack after confirmation."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.set_my_stack_item(user.id, technology, level)
    except DomainError:
        return "Стек не изменён: после подтверждения проверка не пройдена."


async def validate_my_technology_removal(
    ctx: RunContext[AgentDependencies],
    technology: TechnologyName,
) -> None:
    """Check that the caller owns the requested stack entry before approval."""
    user = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_my_stack_item_removal(user.id, technology)
    except DomainError as error:
        raise ModelRetry("Не удалось подготовить удаление. Проверьте название в своём стеке.") from error


async def remove_my_technology(
    ctx: RunContext[AgentDependencies],
    technology: TechnologyName,
) -> str:
    """Remove only one item from the caller's own stack after confirmation."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.remove_my_stack_item(user.id, technology)
    except DomainError:
        return "Стек не изменён: после подтверждения проверка не пройдена."


def stack_confirmation_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Build the user-owned stack confirmation from validated canonical arguments."""
    del recipient
    arguments = call.args_as_dict()
    raw_technology = _text_value(arguments.get("technology"))
    technology = normalize_technology_name(raw_technology) if raw_technology is not None else "указанную технологию"
    if call.tool_name == "remove_my_technology":
        return f"Удалить из вашего стека технологию «{technology}»."
    level = _text_value(arguments.get("level")) or "неуказанный уровень"
    return f"Добавить или изменить в вашем стеке технологию «{technology}», уровень — «{level}»."


def _text_value(value: object) -> str | None:
    return value if isinstance(value, str) else None
