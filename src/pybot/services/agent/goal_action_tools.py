"""Confirmed goal actions limited to the authenticated participant."""

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart

from ...domain.exceptions import DomainError
from ...domain.services.community_checkins import normalize_goal_text
from ...dto.community_checkins import GoalText
from .dependencies import AgentDependencies


async def validate_my_goal(ctx: RunContext[AgentDependencies], text: GoalText) -> None:
    """Validate and normalize an own-goal change before its confirmation is shown."""
    await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_my_goal(text)
    except (ValueError, DomainError) as error:
        raise ModelRetry("Цель должна содержать от 1 до 200 символов.") from error


async def set_my_goal(ctx: RunContext[AgentDependencies], text: GoalText) -> str:
    """Set or replace the caller's own active goal after explicit approval."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.set_my_goal(user.id, text)
    except ValueError:
        return "Цель не изменена: проверьте длину текста."


async def validate_goal_completion(ctx: RunContext[AgentDependencies]) -> None:
    """Ensure the caller has a goal that can be marked as completed."""
    user = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_my_goal_completion(user.id)
    except DomainError as error:
        raise ModelRetry("Сейчас нет активной цели, которую можно отметить достигнутой.") from error


async def mark_my_goal_achieved(ctx: RunContext[AgentDependencies]) -> str:
    """Mark the caller's active goal achieved after explicit approval."""
    user = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.complete_my_goal(user.id)
    except DomainError:
        return "Цель не отмечена: активную цель не удалось найти."


def goal_confirmation_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Render the goal action and its normalized text for the author-only approval card."""
    del recipient
    if call.tool_name == "mark_my_goal_achieved":
        return "Отметить вашу активную цель как достигнутую и добавить достижение «Цель взята»."
    arguments = call.args_as_dict()
    text = arguments.get("text")
    goal = normalize_goal_text(text) if isinstance(text, str) else "указанную цель"
    return f"Установить для вас новую активную цель: «{goal}»."
