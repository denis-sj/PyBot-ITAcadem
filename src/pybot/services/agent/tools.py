"""Explicit tool registry and execution-time authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import to_json

from ...domain.agent import AgentAccessDeniedError
from ...dto import UserReadDTO
from .admin_action_tools import (
    add_manual_attendance_visit,
    adjust_user_points,
    change_user_competence,
    change_user_role,
    confirmation_summary,
    delete_attendance_visit,
    update_attendance_visit_time,
    validate_manual_visit,
    validate_points_adjustment,
    validate_user_competence_change,
    validate_user_role_change,
    validate_visit_delete,
    validate_visit_update,
)
from .broadcast_tools import broadcast_confirmation_summary, send_broadcast, validate_broadcast
from .dependencies import AgentDependencies
from .read_tools import (
    ADMIN_AGENT_ROLES,
    find_user_candidates,
    get_attendance_month_top,
    get_attendance_week_top,
    get_bot_info,
    get_club_attendance_report,
    get_competences_catalog,
    get_current_chat_id,
    get_current_month,
    get_my_attendance,
    get_my_competences,
    get_my_profile,
    get_my_roles,
    get_my_visits,
    get_points_leaderboard,
    get_roles_catalog,
    get_user_attendance,
    get_user_profile,
)
from .role_tools import request_mentor_role, validate_mentor_request

_ACTION_ARGUMENTS_ADAPTER = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class RegisteredAgentTool:
    """A tool's role policy is enforced during discovery and execution."""

    tool: Tool[AgentDependencies]
    allowed_roles: frozenset[str] = frozenset()
    confirmation_summary: Callable[[ToolCallPart, str | None], str] | None = None
    uses_broadcast_roles: bool = False


class AgentToolRegistry:
    """Keep tool policy next to its implementation without global registration."""

    def __init__(
        self,
        entries: tuple[RegisteredAgentTool, ...] | None = None,
        *,
        broadcast_allowed_roles: frozenset[str] = frozenset(),
    ) -> None:
        self.broadcast_allowed_roles = broadcast_allowed_roles
        if entries is None:
            public_tools = tuple(
                RegisteredAgentTool(Tool(function))
                for function in (
                    bot_capabilities,
                    get_my_profile,
                    get_my_attendance,
                    get_my_visits,
                    get_attendance_week_top,
                    get_attendance_month_top,
                    get_points_leaderboard,
                    get_roles_catalog,
                    get_competences_catalog,
                    get_my_roles,
                    get_my_competences,
                    get_bot_info,
                )
            )
            admin_tools = tuple(
                RegisteredAgentTool(Tool(function), ADMIN_AGENT_ROLES)
                for function in (
                    get_current_month,
                    get_club_attendance_report,
                    get_user_profile,
                    get_user_attendance,
                    get_current_chat_id,
                    find_user_candidates,
                )
            )
            admin_action_tools = (
                RegisteredAgentTool(
                    Tool(
                        adjust_user_points,
                        args_validator=validate_points_adjustment,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
                RegisteredAgentTool(
                    Tool(
                        change_user_role,
                        args_validator=validate_user_role_change,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
                RegisteredAgentTool(
                    Tool(
                        change_user_competence,
                        args_validator=validate_user_competence_change,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
                RegisteredAgentTool(
                    Tool(
                        update_attendance_visit_time,
                        args_validator=validate_visit_update,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
                RegisteredAgentTool(
                    Tool(
                        delete_attendance_visit,
                        args_validator=validate_visit_delete,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
                RegisteredAgentTool(
                    Tool(
                        add_manual_attendance_visit,
                        args_validator=validate_manual_visit,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    confirmation_summary,
                ),
            )
            role_request_tool = RegisteredAgentTool(
                Tool(
                    request_mentor_role,
                    args_validator=validate_mentor_request,
                    requires_approval=True,
                ),
                frozenset({"Student"}),
                _role_request_summary,
            )
            broadcast_tool = RegisteredAgentTool(
                Tool(
                    send_broadcast,
                    args_validator=validate_broadcast,
                    requires_approval=True,
                ),
                confirmation_summary=broadcast_confirmation_summary,
                uses_broadcast_roles=True,
            )
            self.entries = (*public_tools, role_request_tool, *admin_tools, *admin_action_tools, broadcast_tool)
        else:
            self.entries = entries
        for entry in self.entries:
            entry.tool.sequential = True
            if entry.tool.requires_approval and entry.confirmation_summary is None:
                raise ValueError("Action tools must define a user-facing confirmation summary")
        names = [entry.tool.name for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate agent tool name")

    async def describe_action(self, call: ToolCallPart, dependencies: AgentDependencies) -> tuple[ToolCallPart, str]:
        """Validate, canonicalize, and describe one action using code-owned wording."""
        for entry in self.entries:
            if entry.tool.name == call.tool_name and entry.confirmation_summary is not None:
                validated_args = entry.tool.function_schema.validator.validate_python(call.args_as_dict())
                canonical_args = _ACTION_ARGUMENTS_ADAPTER.validate_json(to_json(validated_args))
                canonical_call = replace(call, args=canonical_args)
                recipient = await self._recipient_label(canonical_args, dependencies)
                if "telegram_id" in canonical_args and recipient is None:
                    raise AgentAccessDeniedError
                return canonical_call, entry.confirmation_summary(canonical_call, recipient)
        raise AgentAccessDeniedError

    @staticmethod
    async def _recipient_label(arguments: dict[str, JsonValue], dependencies: AgentDependencies) -> str | None:
        """Resolve a model-supplied Telegram ID to the exact persisted user identity."""
        telegram_id = arguments.get("telegram_id")
        if telegram_id is None:
            return None
        if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
            raise AgentAccessDeniedError

        recipient: UserReadDTO = await dependencies.actions.find_user(telegram_id)
        if recipient.telegram_id != telegram_id:
            raise AgentAccessDeniedError
        full_name = " ".join(part for part in (recipient.last_name, recipient.first_name, recipient.patronymic) if part)
        full_name = full_name or "Имя не указано"
        username = f" (@{recipient.telegram_username})" if recipient.telegram_username else ""
        return f"{full_name}{username}, Telegram ID {recipient.telegram_id}"

    def get_policy(self, name: str) -> frozenset[str]:
        """Find a registered tool policy, rejecting unregistered names."""
        for entry in self.entries:
            if entry.tool.name == name:
                if entry.uses_broadcast_roles:
                    if not self.broadcast_allowed_roles:
                        raise AgentAccessDeniedError
                    return self.broadcast_allowed_roles
                return entry.allowed_roles
        raise AgentAccessDeniedError

    def for_roles(self, roles: frozenset[str]) -> AuthorizedAgentToolset:
        """Expose only tools allowed for the current role."""
        entries: list[RegisteredAgentTool] = []
        for entry in self.entries:
            resolved_entry = entry
            if entry.uses_broadcast_roles:
                if not self.broadcast_allowed_roles:
                    continue
                resolved_entry = replace(entry, allowed_roles=self.broadcast_allowed_roles, uses_broadcast_roles=False)
            if not resolved_entry.allowed_roles or roles.intersection(resolved_entry.allowed_roles):
                entries.append(resolved_entry)
        return AuthorizedAgentToolset(tuple(entries))


class AuthorizedAgentToolset(FunctionToolset[AgentDependencies]):
    """Enforce authorization for every call, including resumed approvals."""

    def __init__(self, entries: tuple[RegisteredAgentTool, ...]) -> None:
        super().__init__([entry.tool for entry in entries])
        self.policies = {entry.tool.name: entry.allowed_roles for entry in entries}

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, JsonValue],
        ctx: RunContext[AgentDependencies],
        tool: ToolsetTool[AgentDependencies],
    ) -> str:
        """Reject forged or no-longer-authorized calls before executing code."""
        if name not in self.policies:
            raise AgentAccessDeniedError
        await ctx.deps.require_roles(self.policies[name])
        result = await super().call_tool(name, tool_args, ctx, tool)
        if not isinstance(result, str):
            raise TypeError("Agent tools must return text")
        return result


async def bot_capabilities(ctx: RunContext[AgentDependencies]) -> str:
    """Explain bot functions and available commands for the caller's roles."""
    await ctx.deps.require_roles(frozenset())
    roles = await ctx.deps.get_roles()
    commands = (
        "Профиль и баллы — /profile; посещаемость — /visits; рейтинг — /leaderboard; "
        "роли — /roles и /showroles; компетенции — /competences и /showcompetences; "
        "информация — /info; заявка на роль — /role_request."
    )
    if "Admin" in roles:
        commands += (
            " Администратору: /current_month, /report, /chat_id, /academic_points, /reputation_points, "
            "/addrole, /removerole, /addcompetence, /removecompetence, /edit_visits. "
            "Для импорта посещаемости по фото используйте /report и кнопку добавления часов."
            " Через агента администратор также может читать отчёт, текущий расчётный месяц, ID текущего чата "
            "и профиль или посещаемость пользователя по его Telegram ID. Через агента можно подтвердить начисление "
            "или списание баллов, изменение роли или компетенции, а также правку посещения."
        )
    if roles.intersection(ctx.deps.settings.broadcast_allowed_roles):
        commands += " Рассылка — /broadcast или кнопка рассылки в меню."
    introduction = (
        "Через агента доступны мой профиль и баллы, мои посещения, топ посещаемости недели и месяца, "
        "лидерборд баллов, каталоги ролей и компетенций, мои роли и компетенции, информация о боте. "
        "Инструменты чтения и изменения для администратора доступны только его роли. "
        "Подать заявку на роль Mentor можно через агента после подтверждения. "
    )
    return introduction + commands


def _role_request_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Return code-owned approval text for the fixed Mentor request."""
    del call, recipient
    return "Подать от вашего имени заявку на роль Mentor. После отправки её рассмотрят администраторы."
