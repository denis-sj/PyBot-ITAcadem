"""Explicit tool registry and execution-time authorization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import to_json

from ...domain.agent import AgentAccessDeniedError
from ...domain.exceptions import UserNotFoundError
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
from .goal_action_tools import (
    goal_confirmation_summary,
    mark_my_goal_achieved,
    set_my_goal,
    validate_goal_completion,
    validate_my_goal,
)
from .meeting_action_tools import (
    cancel_community_meeting,
    create_community_meeting,
    meeting_confirmation_summary,
    preview_meeting_cancellation,
    preview_meeting_creation,
    preview_meeting_reschedule,
    reschedule_community_meeting,
    validate_meeting_cancellation,
    validate_meeting_creation,
    validate_meeting_reschedule,
)
from .read_tools import (
    ADMIN_AGENT_ROLES,
    find_technology_helpers,
    find_user_candidates,
    get_attendance_month_top,
    get_attendance_week_top,
    get_bot_info,
    get_club_attendance_report,
    get_competences_catalog,
    get_current_chat_id,
    get_current_month,
    get_meeting_attendees,
    get_my_achievements,
    get_my_attendance,
    get_my_competences,
    get_my_goal,
    get_my_profile,
    get_my_roles,
    get_my_technology_stack,
    get_my_visits,
    get_points_leaderboard,
    get_roles_catalog,
    get_upcoming_meetings,
    get_user_attendance,
    get_user_profile,
)
from .role_tools import request_mentor_role, validate_mentor_request
from .stack_action_tools import (
    remove_my_technology,
    set_my_technology,
    stack_confirmation_summary,
    validate_my_technology_removal,
    validate_my_technology_update,
)

_ACTION_ARGUMENTS_ADAPTER = TypeAdapter(dict[str, JsonValue])
ALL_AGENT_ROLES = frozenset({"Student", "Mentor", "Admin"})


@dataclass(frozen=True)
class ConfiguredAgentRoles:
    """Resolve a tool's roles from a named application setting."""

    setting_name: Literal["broadcast_allowed_roles"]


AgentRolePolicy = frozenset[str] | ConfiguredAgentRoles


@dataclass(frozen=True)
class AgentToolMetadata:
    """Policy and model-facing contract stored beside one registered tool."""

    name: str
    roles: AgentRolePolicy
    kind: Literal["read", "action"]
    requires_confirmation: bool
    description: str
    service: str


@dataclass(frozen=True)
class RegisteredAgentTool:
    """A tool's metadata and confirmation presentation used by the registry."""

    tool: Tool[AgentDependencies]
    metadata: AgentToolMetadata
    confirmation_summary: Callable[[ToolCallPart, str | None], str] | None = None
    confirmation_previewer: Callable[[ToolCallPart, AgentDependencies], Awaitable[str]] | None = None


def _register_tool(  # noqa: PLR0913
    tool: Tool[AgentDependencies],
    roles: AgentRolePolicy,
    kind: Literal["read", "action"],
    service: str,
    *,
    confirmation_summary: Callable[[ToolCallPart, str | None], str] | None = None,
    confirmation_previewer: Callable[[ToolCallPart, AgentDependencies], Awaitable[str]] | None = None,
) -> RegisteredAgentTool:
    """Build one registry row from its executable tool and explicit policy."""
    metadata = AgentToolMetadata(
        name=tool.name,
        roles=roles,
        kind=kind,
        requires_confirmation=tool.requires_approval,
        description=tool.description or "",
        service=service,
    )
    return RegisteredAgentTool(tool, metadata, confirmation_summary, confirmation_previewer)


class AgentToolRegistry:
    """Keep tool policy next to its implementation without global registration."""

    def __init__(  # noqa: C901
        self,
        entries: tuple[RegisteredAgentTool, ...] | None = None,
        *,
        broadcast_allowed_roles: frozenset[str] = frozenset(),
    ) -> None:
        self.broadcast_allowed_roles = broadcast_allowed_roles
        if entries is None:
            public_tools = tuple(
                _register_tool(Tool(function), ALL_AGENT_ROLES, "read", "AgentReadService")
                for function in (
                    get_my_profile,
                    get_my_achievements,
                    get_my_goal,
                    get_my_technology_stack,
                    find_technology_helpers,
                    get_upcoming_meetings,
                    get_my_attendance,
                    get_my_visits,
                    get_attendance_week_top,
                    get_attendance_month_top,
                    get_points_leaderboard,
                    get_roles_catalog,
                    get_competences_catalog,
                    get_my_roles,
                    get_my_competences,
                )
            )
            public_tools = (
                _register_tool(Tool(bot_capabilities), ALL_AGENT_ROLES, "read", "AgentDependencies"),
                _register_tool(Tool(get_meeting_attendees), frozenset({"Admin", "Mentor"}), "read", "AgentReadService"),
                _register_tool(Tool(get_bot_info), ALL_AGENT_ROLES, "read", "INFO_GLOBAL"),
                *public_tools,
            )
            admin_tools = tuple(
                _register_tool(Tool(function), ADMIN_AGENT_ROLES, "read", "AgentReadService")
                for function in (
                    get_current_month,
                    get_club_attendance_report,
                    get_user_profile,
                    get_user_attendance,
                    find_user_candidates,
                )
            )
            admin_tools = (
                *admin_tools,
                _register_tool(Tool(get_current_chat_id), ADMIN_AGENT_ROLES, "read", "AgentDependencies"),
            )
            admin_action_tools = (
                _register_tool(
                    Tool(
                        adjust_user_points,
                        args_validator=validate_points_adjustment,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.adjust_points",
                    confirmation_summary=confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        change_user_role,
                        args_validator=validate_user_role_change,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.change_role",
                    confirmation_summary=confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        change_user_competence,
                        args_validator=validate_user_competence_change,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.change_competence",
                    confirmation_summary=confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        update_attendance_visit_time,
                        args_validator=validate_visit_update,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.update_visit_time",
                    confirmation_summary=confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        delete_attendance_visit,
                        args_validator=validate_visit_delete,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.delete_visit",
                    confirmation_summary=confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        add_manual_attendance_visit,
                        args_validator=validate_manual_visit,
                        requires_approval=True,
                    ),
                    ADMIN_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.create_manual_visit",
                    confirmation_summary=confirmation_summary,
                ),
            )
            role_request_tool = _register_tool(
                Tool(
                    request_mentor_role,
                    args_validator=validate_mentor_request,
                    requires_approval=True,
                ),
                frozenset({"Student"}),
                "action",
                "AgentActionsPort.request_mentor_role",
                confirmation_summary=_role_request_summary,
            )
            stack_action_tools = (
                _register_tool(
                    Tool(
                        set_my_technology,
                        args_validator=validate_my_technology_update,
                        requires_approval=True,
                    ),
                    ALL_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.set_my_stack_item",
                    confirmation_summary=stack_confirmation_summary,
                ),
                _register_tool(
                    Tool(
                        remove_my_technology,
                        args_validator=validate_my_technology_removal,
                        requires_approval=True,
                    ),
                    ALL_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.remove_my_stack_item",
                    confirmation_summary=stack_confirmation_summary,
                ),
            )
            goal_action_tools = (
                _register_tool(
                    Tool(set_my_goal, args_validator=validate_my_goal, requires_approval=True),
                    ALL_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.set_my_goal",
                    confirmation_summary=goal_confirmation_summary,
                ),
                _register_tool(
                    Tool(mark_my_goal_achieved, args_validator=validate_goal_completion, requires_approval=True),
                    ALL_AGENT_ROLES,
                    "action",
                    "AgentActionsPort.complete_my_goal",
                    confirmation_summary=goal_confirmation_summary,
                ),
            )
            meeting_action_tools = (
                _register_tool(
                    Tool(create_community_meeting, args_validator=validate_meeting_creation, requires_approval=True),
                    frozenset({"Admin", "Mentor"}),
                    "action",
                    "AgentActionsPort.create_meeting",
                    confirmation_summary=meeting_confirmation_summary,
                    confirmation_previewer=preview_meeting_creation,
                ),
                _register_tool(
                    Tool(
                        reschedule_community_meeting, args_validator=validate_meeting_reschedule, requires_approval=True
                    ),
                    frozenset({"Admin", "Mentor"}),
                    "action",
                    "AgentActionsPort.reschedule_meeting",
                    confirmation_summary=meeting_confirmation_summary,
                    confirmation_previewer=preview_meeting_reschedule,
                ),
                _register_tool(
                    Tool(
                        cancel_community_meeting, args_validator=validate_meeting_cancellation, requires_approval=True
                    ),
                    frozenset({"Admin", "Mentor"}),
                    "action",
                    "AgentActionsPort.cancel_meeting",
                    confirmation_summary=meeting_confirmation_summary,
                    confirmation_previewer=preview_meeting_cancellation,
                ),
            )
            broadcast_tool = _register_tool(
                Tool(
                    send_broadcast,
                    args_validator=validate_broadcast,
                    requires_approval=True,
                ),
                ConfiguredAgentRoles("broadcast_allowed_roles"),
                "action",
                "AgentActionsPort.broadcast",
                confirmation_summary=broadcast_confirmation_summary,
            )
            self.entries = (
                *public_tools,
                role_request_tool,
                *admin_tools,
                *admin_action_tools,
                *stack_action_tools,
                *goal_action_tools,
                *meeting_action_tools,
                broadcast_tool,
            )
        else:
            self.entries = entries
        for entry in self.entries:
            entry.tool.sequential = True
            metadata = entry.metadata
            if not metadata.name or metadata.name != entry.tool.name:
                raise ValueError("Registered agent tool metadata must match its tool name")
            if not metadata.description or metadata.description != entry.tool.description:
                raise ValueError("Registered agent tools must define one model-facing description")
            if not metadata.service:
                raise ValueError("Registered agent tools must name the service they call")
            if not metadata.roles:
                raise ValueError("Registered agent tools must define an explicit role policy")
            if metadata.kind == "action" and not metadata.requires_confirmation:
                raise ValueError("Action tools must require user confirmation")
            if metadata.kind == "read" and metadata.requires_confirmation:
                raise ValueError("Read tools must not require action confirmation")
            if metadata.requires_confirmation != entry.tool.requires_approval:
                raise ValueError("Registered agent tool confirmation metadata must match its approval policy")
            if metadata.requires_confirmation and entry.confirmation_summary is None:
                raise ValueError("Action tools must define a user-facing confirmation summary")
        names = [entry.tool.name for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate agent tool name")

    async def describe_action(
        self, call: ToolCallPart, dependencies: AgentDependencies
    ) -> tuple[ToolCallPart, str] | None:
        """Validate, canonicalize, and describe one action using code-owned wording."""
        for entry in self.entries:
            if entry.tool.name == call.tool_name and entry.confirmation_summary is not None:
                validated_args = entry.tool.function_schema.validator.validate_python(call.args_as_dict())
                canonical_args = _ACTION_ARGUMENTS_ADAPTER.validate_json(to_json(validated_args))
                canonical_call = replace(call, args=canonical_args)
                recipient = await self._recipient_label(canonical_args, dependencies)
                if "telegram_id" in canonical_args and recipient is None:
                    return None
                if entry.confirmation_previewer is not None:
                    summary = await entry.confirmation_previewer(canonical_call, dependencies)
                else:
                    summary = entry.confirmation_summary(canonical_call, recipient)
                return canonical_call, summary
        raise AgentAccessDeniedError

    @staticmethod
    async def _recipient_label(arguments: dict[str, JsonValue], dependencies: AgentDependencies) -> str | None:
        """Resolve a model-supplied Telegram ID to the exact persisted user identity."""
        telegram_id = arguments.get("telegram_id")
        if telegram_id is None:
            return None
        if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
            raise AgentAccessDeniedError
        if not dependencies.can_resolve_action_recipient(telegram_id):
            return None

        try:
            recipient: UserReadDTO = await dependencies.actions.find_user(telegram_id)
        except UserNotFoundError:
            return None
        if recipient.telegram_id != telegram_id:
            return None
        full_name = " ".join(part for part in (recipient.last_name, recipient.first_name, recipient.patronymic) if part)
        full_name = full_name or "Имя не указано"
        username = f" (@{recipient.telegram_username})" if recipient.telegram_username else ""
        return f"{full_name}{username}, Telegram ID {recipient.telegram_id}"

    def get_policy(self, name: str) -> frozenset[str]:
        """Find a registered tool policy, rejecting unregistered names."""
        for entry in self.entries:
            if entry.metadata.name == name:
                roles = entry.metadata.roles
                if isinstance(roles, ConfiguredAgentRoles):
                    if roles.setting_name != "broadcast_allowed_roles" or not self.broadcast_allowed_roles:
                        raise AgentAccessDeniedError
                    return self.broadcast_allowed_roles
                return roles
        raise AgentAccessDeniedError

    def for_roles(self, actor_roles: frozenset[str]) -> AuthorizedAgentToolset:
        """Expose only tools allowed for the current role."""
        entries: list[RegisteredAgentTool] = []
        for entry in self.entries:
            resolved_entry = entry
            role_policy = entry.metadata.roles
            if isinstance(role_policy, ConfiguredAgentRoles):
                if not self.broadcast_allowed_roles:
                    continue
                resolved_entry = replace(
                    entry,
                    metadata=replace(entry.metadata, roles=self.broadcast_allowed_roles),
                )
                role_policy = self.broadcast_allowed_roles
            if actor_roles.intersection(role_policy):
                entries.append(resolved_entry)
        return AuthorizedAgentToolset(tuple(entries))


class AuthorizedAgentToolset(FunctionToolset[AgentDependencies]):
    """Enforce authorization for every call, including resumed approvals."""

    def __init__(self, entries: tuple[RegisteredAgentTool, ...]) -> None:
        super().__init__([entry.tool for entry in entries])
        policies: dict[str, frozenset[str]] = {}
        for entry in entries:
            roles = entry.metadata.roles
            if isinstance(roles, ConfiguredAgentRoles) or not roles:
                raise ValueError("Toolsets require resolved, explicit tool role policies")
            policies[entry.metadata.name] = roles
        self.policies = policies

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
        "мои достижения и серия — через вопрос «мои достижения»; моя цель — через вопрос агенту; "
        "уведомления — /notifications, чекины — /pause_checkin и /resume_checkin; "
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
        "Через агента доступны мой профиль и баллы, мои достижения и серия, моя цель, мои посещения, "
        "мой стек технологий и поиск помощников, "
        "топ посещаемости недели и месяца, "
        "лидерборд баллов, каталоги ролей и компетенций, мои роли и компетенции, информация о боте. "
        "Инструменты администратора доступны только его роли, а изменение собственного стека и цели "
        "требует подтверждения. "
        "Подать заявку на роль Mentor можно через агента после подтверждения. "
    )
    return introduction + commands


def _role_request_summary(call: ToolCallPart, recipient: str | None) -> str:
    """Return code-owned approval text for the fixed Mentor request."""
    del call, recipient
    return "Подать от вашего имени заявку на роль Mentor. После отправки её рассмотрят администраторы."
