"""Broadcast tools prepare a complete preview and send only after approval."""

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart

from ...domain.exceptions import BroadcastAlreadyRunningError, DomainError, UsersNotFoundError
from ..ports.agent import AgentBroadcastAudience
from .dependencies import AgentDependencies


async def validate_broadcast(
    ctx: RunContext[AgentDependencies],
    message: Annotated[str, Field(min_length=1, max_length=3500, strict=True)],
    audience: AgentBroadcastAudience = "all",
    target: Annotated[str | None, Field(max_length=100, strict=True)] = None,
) -> None:
    """Validate the target and exact text before presenting a confirmation to a permitted sender."""
    if not _is_private_chat(ctx):
        raise ModelRetry("Подготовить рассылку через агента можно только в личном чате с ботом.")
    try:
        await ctx.deps.actions.validate_broadcast(audience, target, message)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить рассылку. Проверьте текст, роль или компетенцию получателей."
        ) from error


async def send_broadcast(
    ctx: RunContext[AgentDependencies],
    message: Annotated[str, Field(min_length=1, max_length=3500, strict=True)],
    audience: AgentBroadcastAudience = "all",
    target: Annotated[str | None, Field(max_length=100, strict=True)] = None,
) -> str:
    """Send a prepared broadcast only after the user confirms the exact audience and text."""
    if not _is_private_chat(ctx):
        return "Рассылка через агента доступна только в личном чате с ботом. Сообщение не отправлено."
    try:
        return await ctx.deps.actions.broadcast(audience, target, message)
    except BroadcastAlreadyRunningError:
        return "Другая рассылка уже выполняется. Эта рассылка не отправлена."
    except UsersNotFoundError:
        return "Получатели не найдены. Рассылка не отправлена."
    except DomainError:
        return "Не удалось отправить рассылку: проверка не пройдена, сообщение не отправлено."


def broadcast_confirmation_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Show a code-owned recipient description and the full exact text being approved."""
    del recipient
    arguments = call.args_as_dict()
    audience = arguments.get("audience")
    target = arguments.get("target")
    message = arguments.get("message")

    if audience == "all":
        recipients = "всем зарегистрированным пользователям"
    elif audience == "role" and isinstance(target, str):
        recipients = f"всем участникам с ролью {target}"
    elif audience == "competence" and isinstance(target, str):
        recipients = f"всем участникам с компетенцией «{target}»"
    else:
        recipients = "указанным получателям"

    exact_message = message if isinstance(message, str) else ""
    return f"Отправить рассылку {recipients}.\nТекст:\n---\n{exact_message}\n---"


def _is_private_chat(ctx: RunContext[AgentDependencies]) -> bool:
    """Match /broadcast's private-chat restriction using Telegram's private chat identity."""
    return ctx.deps.conversation.chat_id == ctx.deps.conversation.telegram_id
