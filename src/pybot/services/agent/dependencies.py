"""Authenticated request context shared by agent tools."""

from dataclasses import dataclass

from ...core.config import AppSettings
from ...domain.agent import AgentAccessDeniedError
from ...dto import UserReadDTO
from ...dto.agent import AgentConversation
from ..ports.agent import AgentActionsPort
from ..user_services.users import UserService
from .reads import AgentReadService


@dataclass
class AgentDependencies:
    """Resolve the actor from transport context, never model arguments."""

    conversation: AgentConversation
    users: UserService
    settings: AppSettings
    reads: AgentReadService
    actions: AgentActionsPort

    async def get_actor(self) -> UserReadDTO:
        """Re-check registration immediately before reading personal data."""
        user = await self.users.find_user_by_telegram_id(self.conversation.telegram_id)
        if user is None:
            raise AgentAccessDeniedError
        return user

    async def get_roles(self) -> frozenset[str]:
        """Fetch current roles, including when a deferred request resumes."""
        user = await self.get_actor()
        return frozenset(await self.users.find_all_user_roles(user.id) or ())

    async def require_roles(self, allowed_roles: frozenset[str]) -> None:
        """Enforce execution-time authorization independently of the model."""
        roles = await self.get_roles()
        if allowed_roles and not roles.intersection(allowed_roles):
            raise AgentAccessDeniedError
