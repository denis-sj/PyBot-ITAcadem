"""Administrator action tools, with validation before and after explicit approval."""

from datetime import date, time
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart

from ...core.constants import PointsTypeEnum, RoleEnum
from ...domain.exceptions import DomainError
from .dependencies import AgentDependencies


async def validate_points_adjustment(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    points_type: PointsTypeEnum,
    delta: int,
    reason: Annotated[str | None, Field(max_length=250)] = None,
) -> None:
    """Check a points change and balance before asking the administrator to confirm."""
    del reason
    admin = await ctx.deps.get_actor()
    try:
        await ctx.deps.actions.validate_points_adjustment(admin.id, telegram_id, points_type, delta)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить изменение баллов. Проверьте Telegram ID, сумму и баланс участника."
        ) from error


async def adjust_user_points(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    points_type: PointsTypeEnum,
    delta: int,
    reason: Annotated[str | None, Field(max_length=250)] = None,
) -> str:
    """Начислить или списать академические либо репутационные баллы после подтверждения."""
    admin = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.adjust_points(admin.id, telegram_id, points_type, delta, reason)
    except DomainError:
        return "Баллы не изменены: после подтверждения изменились данные или проверка не пройдена."


async def validate_user_role_change(
    ctx: RunContext[AgentDependencies], telegram_id: int, role: RoleEnum, add: bool
) -> None:
    """Check recipient and current role assignment before confirmation."""
    try:
        await ctx.deps.actions.validate_role_change(telegram_id, role, add)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить изменение роли. Проверьте Telegram ID, каталог ролей и текущую роль."
        ) from error


async def change_user_role(ctx: RunContext[AgentDependencies], telegram_id: int, role: RoleEnum, add: bool) -> str:
    """Issue or remove a role after confirmation using the existing role service."""
    try:
        return await ctx.deps.actions.change_role(telegram_id, role, add)
    except DomainError:
        return "Роль не изменена: после подтверждения изменились данные или проверка не пройдена."


async def validate_user_competence_change(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    competence_name: Annotated[str, Field(min_length=1, max_length=100)],
    add: bool,
) -> None:
    """Check the requested competence and membership before confirmation."""
    try:
        await ctx.deps.actions.validate_competence_change(telegram_id, competence_name, add)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить изменение компетенции. Проверьте Telegram ID, название и текущий список."
        ) from error


async def change_user_competence(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    competence_name: Annotated[str, Field(min_length=1, max_length=100)],
    add: bool,
) -> str:
    """Issue or remove a competence after confirmation using existing services."""
    try:
        return await ctx.deps.actions.change_competence(telegram_id, competence_name, add)
    except DomainError:
        return "Компетенция не изменена: после подтверждения изменились данные или проверка не пройдена."


async def validate_visit_update(
    ctx: RunContext[AgentDependencies], telegram_id: int, visit_date: date, start_time: time, end_time: time
) -> None:
    """Check an existing visit and the proposed interval before confirmation."""
    try:
        await ctx.deps.actions.validate_visit_update(telegram_id, visit_date, start_time, end_time)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить изменение посещения. Проверьте Telegram ID, дату и интервал времени."
        ) from error


async def update_attendance_visit_time(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    visit_date: date,
    start_time: time,
    end_time: time,
) -> str:
    """Change a visit time after confirmation and write the standard audit history."""
    admin = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.update_visit_time(telegram_id, visit_date, start_time, end_time, admin)
    except DomainError:
        return "Посещение не изменено: после подтверждения запись изменилась или проверка не пройдена."


async def validate_visit_delete(ctx: RunContext[AgentDependencies], telegram_id: int, visit_date: date) -> None:
    """Check an existing visit before confirmation to delete it."""
    try:
        await ctx.deps.actions.validate_visit_delete(telegram_id, visit_date)
    except DomainError as error:
        raise ModelRetry("Не удалось подготовить удаление. Проверьте Telegram ID и дату посещения.") from error


async def delete_attendance_visit(ctx: RunContext[AgentDependencies], telegram_id: int, visit_date: date) -> str:
    """Delete a visit after confirmation and write the standard audit history."""
    admin = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.delete_visit(telegram_id, visit_date, admin)
    except DomainError:
        return "Посещение не удалено: после подтверждения запись изменилась или проверка не пройдена."


async def validate_manual_visit(
    ctx: RunContext[AgentDependencies], telegram_id: int, visit_date: date, start_time: time, end_time: time
) -> None:
    """Check a proposed manual visit before confirmation."""
    try:
        await ctx.deps.actions.validate_manual_visit(telegram_id, visit_date, start_time, end_time)
    except DomainError as error:
        raise ModelRetry(
            "Не удалось подготовить добавление посещения. Проверьте Telegram ID, дату и интервал времени."
        ) from error


async def add_manual_attendance_visit(
    ctx: RunContext[AgentDependencies],
    telegram_id: int,
    visit_date: date,
    start_time: time,
    end_time: time,
) -> str:
    """Add a manual visit after confirmation through the audited attendance service."""
    admin = await ctx.deps.get_actor()
    try:
        return await ctx.deps.actions.create_manual_visit(telegram_id, visit_date, start_time, end_time, admin)
    except DomainError:
        return "Посещение не добавлено: после подтверждения данные изменились или проверка не пройдена."


def confirmation_summary(call: ToolCallPart) -> str:
    """Build a code-owned summary from the exact validated arguments awaiting approval."""
    arguments = call.args_as_dict()
    telegram_id = _int_value(arguments.get("telegram_id"))
    recipient = f"Telegram ID {telegram_id}" if telegram_id is not None else "указанному участнику"

    if call.tool_name == "adjust_user_points":
        delta = _int_value(arguments.get("delta"))
        points_type = _text_value(arguments.get("points_type"))
        reason = _text_value(arguments.get("reason"))
        points_name = "академических" if points_type == PointsTypeEnum.ACADEMIC.value else "репутационных"
        action = "начислить" if delta is not None and delta > 0 else "списать"
        amount = str(abs(delta)) if delta is not None else "указанное количество"
        summary = f"{action} участнику {recipient} {amount} {points_name} баллов"
        return f"{summary}. Причина: «{reason}»." if reason else f"{summary}."

    if call.tool_name == "change_user_role":
        role = _text_value(arguments.get("role")) or "указанную"
        action = "выдать" if arguments.get("add") is True else "снять"
        return f"{action} участнику {recipient} роль {role}."

    if call.tool_name == "change_user_competence":
        competence = _text_value(arguments.get("competence_name")) or "указанную"
        action = "выдать" if arguments.get("add") is True else "снять"
        return f"{action} участнику {recipient} компетенцию «{competence}»."

    visit_date = _text_value(arguments.get("visit_date")) or "указанную дату"
    if call.tool_name == "delete_attendance_visit":
        return f"Удалить посещение участника {recipient} за {visit_date}."

    start_time = _format_time(arguments.get("start_time"))
    end_time = _format_time(arguments.get("end_time"))
    interval = f"{start_time}–{end_time}" if start_time and end_time else "указанный интервал"
    if call.tool_name == "update_attendance_visit_time":
        return f"Изменить посещение участника {recipient} за {visit_date} на {interval}."
    return f"Добавить посещение участника {recipient} за {visit_date}: {interval}."


def _int_value(value: int | str | bool | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_value(value: int | str | bool | None) -> str | None:
    return value if isinstance(value, str) else None


def _format_time(value: int | str | bool | None) -> str | None:
    raw_value = _text_value(value)
    if raw_value is None:
        return None
    try:
        return time.fromisoformat(raw_value).strftime("%H:%M")
    except ValueError:
        return None
