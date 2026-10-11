"""Explicit tool registry and execution-time authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from ...domain.agent import AgentAccessDeniedError
from .dependencies import AgentDependencies
from .read_tools import (
    ADMIN_AGENT_ROLES,
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


@dataclass(frozen=True)
class RegisteredAgentTool:
    """A tool's role policy is enforced during discovery and execution."""

    tool: Tool[AgentDependencies]
    allowed_roles: frozenset[str] = frozenset()
    confirmation_summary: Callable[[ToolCallPart], str] | None = None


class AgentToolRegistry:
    """Keep tool policy next to its implementation without global registration."""

    def __init__(self, entries: tuple[RegisteredAgentTool, ...] | None = None) -> None:
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
                )
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
            self.entries = (*public_tools, role_request_tool, *admin_tools)
        else:
            self.entries = entries
        for entry in self.entries:
            if entry.tool.requires_approval and entry.confirmation_summary is None:
                raise ValueError("Action tools must define a user-facing confirmation summary")
        names = [entry.tool.name for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate agent tool name")

    def describe_action(self, call: ToolCallPart) -> str:
        """Describe business intent using code-owned wording, never model-generated consent."""
        for entry in self.entries:
            if entry.tool.name == call.tool_name and entry.confirmation_summary is not None:
                return entry.confirmation_summary(call)
        raise AgentAccessDeniedError

    def get_policy(self, name: str) -> frozenset[str]:
        """Find a registered tool policy, rejecting unregistered names."""
        for entry in self.entries:
            if entry.tool.name == name:
                return entry.allowed_roles
        raise AgentAccessDeniedError

    def for_roles(self, roles: frozenset[str]) -> AuthorizedAgentToolset:
        """Expose only tools allowed for the current role."""
        entries = tuple(
            entry for entry in self.entries if not entry.allowed_roles or roles.intersection(entry.allowed_roles)
        )
        return AuthorizedAgentToolset(entries)


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
            "и профиль или посещаемость пользователя по его Telegram ID."
        )
    if roles.intersection(ctx.deps.settings.broadcast_allowed_roles):
        commands += " Рассылка — /broadcast или кнопка рассылки в меню."
    introduction = (
        "Через агента доступны мой профиль и баллы, мои посещения, топ посещаемости недели и месяца, "
        "лидерборд баллов, каталоги ролей и компетенций, мои роли и компетенции, информация о боте. "
        "Инструменты чтения для администратора доступны только его роли. "
        "Подать заявку на роль Mentor можно через агента после подтверждения. "
    )
    return introduction + commands


def _role_request_summary(call: ToolCallPart) -> str:
    """Return code-owned approval text for the fixed Mentor request."""
    del call
    return "Подать от вашего имени заявку на роль Mentor. После отправки её рассмотрят администраторы."
