from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.exceptions import UserNotFoundError
from ...dto import UserReadDTO
from ...infrastructure.user_repository import UserRepository
from ...mappers.user_mappers import map_orm_user_to_user_read_dto


class UserService:
    """Shared application service for user lookup and activity tracking."""

    def __init__(
        self,
        db: AsyncSession,
        user_repository: UserRepository,
    ) -> None:
        """Initialize the user service."""
        self.db: AsyncSession = db
        self.user_repository: UserRepository = user_repository

    async def get_user(
        self,
        user_id: int,
    ) -> UserReadDTO:
        """Return a user by internal id or raise a domain error."""
        try:
            user = await self.user_repository.get_by_id(self.db, user_id)
        except UserNotFoundError as err:
            raise UserNotFoundError(user_id) from err
        else:
            return await map_orm_user_to_user_read_dto(user)

    async def find_all_user_roles(self, user_id: int) -> set[str] | None:
        """Return all user role names when present."""
        roles = await self.user_repository.find_all_user_roles_by_pk(self.db, user_id)
        return roles if roles else None

    async def find_user_by_phone(
        self,
        phone: str,
    ) -> UserReadDTO | None:
        """Find a user by normalized phone number."""
        user = await self.user_repository.find_user_by_phone(self.db, phone)
        if user:
            return await map_orm_user_to_user_read_dto(user)
        return None

    async def find_user_by_telegram_id(self, tg_id: int) -> UserReadDTO | None:
        """Find a user by Telegram id."""
        user = await self.user_repository.find_user_by_telegram_id(self.db, tg_id)
        if user:
            return await map_orm_user_to_user_read_dto(user)
        return None

    # TODO Вынести в UserActivityService, если activity flow обрастет собственной orchestration
    async def track_activity(self, telegram_id: int, username: str | None) -> int | None:
        """Update the last activity timestamp for the Telegram user."""
        user = await self.user_repository.find_user_by_telegram_id(self.db, telegram_id)
        if not user:
            return None

        if user.telegram_username != username:
            user.telegram_username = username
        await self.user_repository.update_user_last_active(self.db, user.id)
        await self.db.commit()
        return user.id
