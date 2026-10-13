"""Authenticated request context shared by agent tools."""

import re
from dataclasses import dataclass, field

from ...core.config import AppSettings
from ...domain.agent import AgentAccessDeniedError
from ...dto import UserReadDTO
from ...dto.agent import AgentConversation
from ...dto.agent_read import AgentUserSearchResult
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
    question: str = ""
    searched_recipient_ids: set[int] = field(default_factory=set)
    ambiguous_recipient_search: bool = False
    last_recipient_search_result: AgentUserSearchResult | None = None

    def record_recipient_search(self, result: AgentUserSearchResult) -> None:
        """Retain candidate identity and ambiguity instead of trusting generated arguments."""
        self.last_recipient_search_result = result
        if result.total_matches != 1 or len(result.candidates) != 1:
            self.ambiguous_recipient_search = True
            return
        self.searched_recipient_ids.add(result.candidates[0].telegram_id)

    def can_resolve_action_recipient(self, telegram_id: int) -> bool:
        """Accept a unique search result or a Telegram ID stated in this user turn."""
        if telegram_id <= 0 or self.ambiguous_recipient_search:
            return False
        if self.searched_recipient_ids:
            return self.searched_recipient_ids == {telegram_id}
        label = r"(?:telegram[\s_-]*id|(?:id|ид)[\s_-]*telegram|телеграм(?:а)?[\s_-]*ид)"
        recipient_id = re.escape(str(telegram_id))
        return re.search(rf"{label}\s*(?:[:=#№-]\s*)?{recipient_id}(?!\d)", self.question, re.IGNORECASE) is not None

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
