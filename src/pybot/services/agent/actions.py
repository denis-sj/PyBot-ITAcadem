"""Agent-facing action orchestration over existing application services."""

from ...core.constants import RoleEnum
from ...dto.role_dto import CreateRoleRequestDTO
from ..role_request import RoleRequestService


class AgentActionService:
    """Run explicitly requested agent actions through established use cases."""

    def __init__(self, role_requests: RoleRequestService) -> None:
        self.role_requests = role_requests

    async def validate_role_request(self, user_id: int) -> None:
        """Check current eligibility before asking the user to confirm a role request."""
        await self.role_requests.check_requesting_user(user_id, RoleEnum.MENTOR.value)

    async def request_mentor_role(self, user_id: int) -> CreateRoleRequestDTO:
        """Create the role request after approval; the application service revalidates eligibility."""
        return await self.role_requests.create_role_request(user_id, RoleEnum.MENTOR.value)
