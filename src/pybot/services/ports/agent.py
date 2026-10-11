"""Ports for agent model construction and text-only conversation storage."""

from datetime import date, time
from typing import Protocol

from pydantic_ai.models import Model

from ...core.constants import PointsTypeEnum, RoleEnum
from ...dto import UserReadDTO
from ...dto.agent import AgentConversation, AgentMemoryMessage
from ...dto.role_dto import CreateRoleRequestDTO


class AgentModelPort(Protocol):
    """Construct the configured model without making network requests."""

    def get_model(self) -> Model:
        """Return an OpenAI-compatible model."""
        ...


class AgentMemoryPort(Protocol):
    """Store only user questions and final answers, never tool traces."""

    async def load(self, conversation: AgentConversation) -> list[AgentMemoryMessage]:
        """Load the bounded text history."""
        ...

    async def append(self, conversation: AgentConversation, question: str, answer: str) -> None:
        """Append a turn and renew its TTL."""
        ...

    async def mark_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> None:
        """Record a message sent by the agent."""
        ...

    async def is_agent_reply(self, chat_id: int, topic_id: int | None, message_id: int) -> bool:
        """Check provenance independently of conversation memory expiration."""
        ...


class AgentActionsPort(Protocol):
    """Preflight and execute confirmed agent actions through application services."""

    async def validate_role_request(self, user_id: int) -> None:
        """Check eligibility for the actor's role request."""
        ...

    async def request_mentor_role(self, user_id: int) -> CreateRoleRequestDTO:
        """Create the actor's Mentor request after approval."""
        ...

    async def find_user(self, telegram_id: int) -> UserReadDTO:
        """Find a registered user by exact Telegram identity."""
        ...

    async def validate_points_adjustment(
        self, giver_id: int, telegram_id: int, points_type: PointsTypeEnum, delta: int
    ) -> None:
        """Check an adjustment before confirmation."""
        ...

    async def adjust_points(
        self, giver_id: int, telegram_id: int, points_type: PointsTypeEnum, delta: int, reason: str | None
    ) -> str:
        """Adjust points after confirmation."""
        ...

    async def validate_role_change(self, telegram_id: int, role: RoleEnum, add: bool) -> None:
        """Check a role change before confirmation."""
        ...

    async def change_role(self, telegram_id: int, role: RoleEnum, add: bool) -> str:
        """Change a role after confirmation."""
        ...

    async def validate_competence_change(self, telegram_id: int, competence_name: str, add: bool) -> None:
        """Check a competence change before confirmation."""
        ...

    async def change_competence(self, telegram_id: int, competence_name: str, add: bool) -> str:
        """Change a competence after confirmation."""
        ...

    async def validate_visit_update(self, telegram_id: int, visit_date: date, start_time: time, end_time: time) -> None:
        """Check an attendance edit before confirmation."""
        ...

    async def update_visit_time(
        self, telegram_id: int, visit_date: date, start_time: time, end_time: time, admin: UserReadDTO
    ) -> str:
        """Update attendance after confirmation."""
        ...

    async def validate_visit_delete(self, telegram_id: int, visit_date: date) -> None:
        """Check attendance deletion before confirmation."""
        ...

    async def delete_visit(self, telegram_id: int, visit_date: date, admin: UserReadDTO) -> str:
        """Delete attendance after confirmation."""
        ...

    async def validate_manual_visit(self, telegram_id: int, visit_date: date, start_time: time, end_time: time) -> None:
        """Check a manual attendance entry before confirmation."""
        ...

    async def create_manual_visit(
        self, telegram_id: int, visit_date: date, start_time: time, end_time: time, admin: UserReadDTO
    ) -> str:
        """Create a manual attendance entry after confirmation."""
        ...
