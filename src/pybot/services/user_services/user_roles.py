from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ...core import logger
from ...core.constants import RequestStatus, RoleEnum
from ...domain.exceptions import RoleNotFoundError, UserNotFoundError
from ...dto import RoleReadDTO, UserReadDTO
from ...infrastructure.role_repository import RoleRepository
from ...infrastructure.roles import RoleRequestRepository
from ...infrastructure.user_repository import UserRepository
from ...mappers.user_mappers import map_orm_user_to_user_read_dto
from ..ports import CommunityAchievementDispatchPort, NotificationPort


class UserRolesService:
    """Сервис для управления ролями пользователей.

    Обеспечивает бизнес-логику для проверки, добавления и удаления ролей.
    """

    def __init__(  # noqa: PLR0913
        self,
        db: AsyncSession,
        user_repository: UserRepository,
        role_repository: RoleRepository,
        role_request_repository: RoleRequestRepository | None = None,
        notification_service: NotificationPort | None = None,
        achievement_dispatch_port: CommunityAchievementDispatchPort | None = None,
    ) -> None:
        """Инициализирует сервис ролей пользователя."""
        self.db: AsyncSession = db
        self.user_repository: UserRepository = user_repository
        self.role_repository: RoleRepository = role_repository
        self.role_request_repository: RoleRequestRepository | None = role_request_repository
        self.notification_service: NotificationPort | None = notification_service
        self._achievement_dispatch_port = achievement_dispatch_port

    async def check_user_role(
        self,
        user_id: int,
        user_role: str,
    ) -> bool:
        """Проверяет, обладает ли пользователь указанной ролью.

        Args:
            user_id: Идентификатор пользователя.
            user_role: Название роли для проверки.

        Returns:
            bool: True, если у пользователя есть роль, иначе False.
        """
        return await self.user_repository.has_role(self.db, user_id, user_role)

    async def remove_user_role(self, tg_id: int, role_name: str) -> UserReadDTO:
        """Удаляет роль у пользователя по его Telegram ID.

        Args:
            tg_id: Telegram ID пользователя.
            role_name: Название удаляемой роли.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            RoleNotFoundError: Если роль не найдена.

        Returns:
            UserReadDTO: Обновленный DTO пользователя.
        """
        try:
            user = await self.user_repository.get_by_telegram_id(self.db, tg_id)
        except UserNotFoundError as err:
            raise UserNotFoundError(telegram_id=tg_id) from err

        role = await self.role_repository.find_role_by_name(self.db, role_name)
        if not role:
            raise RoleNotFoundError(f"Роль '{role_name}' не найдена в базе данных.")

        user.remove_role(role)
        await self.db.commit()
        return await map_orm_user_to_user_read_dto(user)

    async def find_user_roles(
        self,
        user_id: int,
    ) -> Sequence[str]:
        """Возвращает список названий ролей пользователя.

        Args:
            user_id: Идентификатор пользователя.

        Returns:
            Sequence[str]: Список названий ролей.
        """
        return await self.user_repository.find_user_roles(self.db, user_id)

    async def find_all_roles(self) -> Sequence[RoleReadDTO]:
        """Возвращает список всех доступных ролей в системе.

        Returns:
            Sequence[RoleReadDTO]: Список DTO ролей.
        """
        roles = await self.role_repository.find_all_roles(self.db)
        return [
            RoleReadDTO(
                id=role.id,
                name=role.name,
                description=role.description,
            )
            for role in roles
        ]

    async def add_user_role(
        self,
        telegram_id: int,
        new_role: RoleEnum,
    ) -> UserReadDTO:
        """Добавляет роль пользователю по его Telegram ID.

        Args:
            telegram_id: Telegram ID пользователя.
            new_role: Роль для добавления (Enum).

        Raises:
            UserNotFoundError: Если пользователь не найден.
            RoleNotFoundError: Если роль не найдена.

        Returns:
            UserReadDTO: Обновленный DTO пользователя.
        """
        try:
            user = await self.user_repository.get_by_telegram_id(self.db, telegram_id)
        except UserNotFoundError as err:
            raise UserNotFoundError(telegram_id=telegram_id) from err

        role = await self.role_repository.find_role_by_name(self.db, new_role.value)
        if not role:
            raise RoleNotFoundError(new_role.value)

        mentor_was_assigned = new_role == RoleEnum.MENTOR and await self.user_repository.has_role(
            self.db,
            user.id,
            RoleEnum.MENTOR.value,
        )

        user.add_role(role)

        closed_request_id: int | None = None
        if self.role_request_repository is not None:
            pending_request = await self.role_request_repository.find_recent_active_request(self.db, user.id)
            if pending_request is not None and pending_request.role_id == role.id:
                pending_request.change_status(RequestStatus.APPROVED)
                self.db.add(pending_request)
                closed_request_id = pending_request.id

        await self.db.commit()

        if new_role == RoleEnum.MENTOR and not mentor_was_assigned:
            await self._queue_achievement_recalculation(user.id)

        if closed_request_id is not None and self.notification_service is not None:
            try:
                await self.notification_service.update_role_request_message(
                    request_id=closed_request_id,
                    status_text="Запрос закрыт: роль выдана командой /addrole.",
                )
            except Exception as exc:
                logger.warning(
                    "Failed to update role request message in Telegram | request_id={request_id} error={error}",
                    request_id=closed_request_id,
                    error=str(exc),
                )

        return await map_orm_user_to_user_read_dto(user)

    async def _queue_achievement_recalculation(self, user_id: int) -> None:
        """Queue Mentor achievement calculation after role assignment commits."""
        if self._achievement_dispatch_port is None:
            return
        try:
            await self._achievement_dispatch_port.recalculate_user_achievements(user_id)
        except Exception:
            logger.exception(
                "Failed to dispatch Mentor achievement recalculation | user_id={user_id}",
                user_id=user_id,
            )
