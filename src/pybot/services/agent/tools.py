"""Explicit tool registry and execution-time authorization."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from ...core.config import AppSettings
from ...domain.agent import AgentAccessDeniedError
from ...dto.agent import AgentConversation
from ..user_services.users import UserService


@dataclass
class AgentDependencies:
    """Request-scoped services and authenticated transport context."""

    conversation: AgentConversation
    users: UserService
    settings: AppSettings

    async def get_roles(self) -> frozenset[str]:
        """Read current roles from services rather than trusting model arguments."""
        user = await self.users.find_user_by_telegram_id(self.conversation.telegram_id)
        if user is None:
            raise AgentAccessDeniedError
        return frozenset(await self.users.find_all_user_roles(user.id) or ())

    async def require_roles(self, allowed_roles: frozenset[str]) -> None:
        """Re-check the actor at the moment a tool executes."""
        roles = await self.get_roles()
        if allowed_roles and not roles.intersection(allowed_roles):
            raise AgentAccessDeniedError


@dataclass(frozen=True)
class RegisteredAgentTool:
    """A tool's role policy is enforced during discovery and execution."""

    tool: Tool[AgentDependencies]
    allowed_roles: frozenset[str] = frozenset()
    confirmation_summary: Callable[[ToolCallPart], str] | None = None


class AgentToolRegistry:
    """Keep tool policy next to its implementation without global registration."""

    def __init__(self, entries: tuple[RegisteredAgentTool, ...] | None = None) -> None:
        self.entries = entries if entries is not None else (RegisteredAgentTool(Tool(bot_capabilities)),)
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
        )
    if roles.intersection(ctx.deps.settings.broadcast_allowed_roles):
        commands += " Рассылка — /broadcast или кнопка рассылки в меню."
    introduction = (
        "Через агента сейчас доступна справка о функциях бота. Остальные функции открывайте командами или меню. "
    )
    return introduction + commands
