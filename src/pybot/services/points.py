from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.constants import PointsTypeEnum
from ..db.models import PointsTransaction, Valuation
from ..domain.exceptions import InsufficientPointsBalanceError, UserNotFoundError
from ..domain.services.level_calculator import LevelCalculator
from ..dto import AdjustUserPointsDTO, UserReadDTO
from ..infrastructure.level_repository import LevelRepository
from ..infrastructure.points_transaction_repository import PointsTransactionRepository
from ..infrastructure.user_repository import UserRepository
from ..mappers.user_mappers import map_orm_user_to_user_read_dto


class PointsService:
    """Сервис для начисления и списания очков пользователей.

    Обеспечивает бизнес-логику для изменения баланса очков пользователей,
    пересчета уровней и записи транзакций.
    """

    def __init__(
        self,
        db: AsyncSession,
        level_calculator: LevelCalculator,
        user_repository: UserRepository,
        level_repository: LevelRepository,
        points_transaction_repository: PointsTransactionRepository,
    ) -> None:
        """Инициализирует сервис очков.

        Args:
            db: Асинхронная сессия базы данных.
            level_calculator: Сервис для вычисления уровней пользователя.
            user_repository: Репозиторий для работы с пользователями.
            level_repository: Репозиторий для работы с уровнями.
            points_transaction_repository: Репозиторий для работы с транзакциями очков.
        """
        self.db: AsyncSession = db
        self.level_calculator: LevelCalculator = level_calculator
        self.user_repository: UserRepository = user_repository
        self.level_repository: LevelRepository = level_repository
        self.points_transaction_repository: PointsTransactionRepository = points_transaction_repository

    async def change_points(self, dto: AdjustUserPointsDTO) -> UserReadDTO:
        """Изменяет количество очков пользователя.

        Начисляет или списывает очки, пересчитывает уровень и создает
        соответствующие записи о транзакции и оценке (Valuation).

        Args:
            dto: DTO с данными для изменения очков (кто, кому, сколько, причина).

        Raises:
            UserNotFoundError: Если пользователь, получающий очки, или тот, кто их выдает, не найдены.

        Returns:
            UserReadDTO: DTO с обновленными данными пользователя-получателя.
        """
        try:
            user = await self.user_repository.get_by_id(self.db, dto.recipient_id)
        except UserNotFoundError as err:
            raise UserNotFoundError(user_id=dto.recipient_id) from err

        current_balance = (
            user.academic_points if dto.points.point_type == PointsTypeEnum.ACADEMIC else user.reputation_points
        )
        if dto.points.value < 0 and abs(dto.points.value) > current_balance:
            raise InsufficientPointsBalanceError(
                requested=abs(dto.points.value),
                current=current_balance,
            )

        actual_delta, new_score = user.change_user_points(dto.points.value, dto.points.point_type)

        if dto.points.value > 0:
            all_levels = await self.level_repository.find_all_by_type(self.db, dto.points.point_type)
            new_level = self.level_calculator.calculate_level(new_score, all_levels)
            if new_level is not None:
                current_user_level = next(
                    (ul for ul in user.user_levels if ul.level and ul.level.level_type == dto.points.point_type),
                    None,
                )
                current_level = current_user_level.level if current_user_level else None
                if current_level is None or new_level.required_points > current_level.required_points:
                    user.change_user_level(new_level.id, dto.points.point_type)
            else:
                logger.info("Пользователь достиг максимального уровня")
        try:
            giver_orm = await self.user_repository.get_by_id(self.db, dto.giver_id)
        except UserNotFoundError as err:
            raise UserNotFoundError(user_id=dto.giver_id) from err

        valuation = Valuation.create(
            recipient=user,
            giver=giver_orm,
            points=dto.points,
            reason=dto.reason,
        )
        points_transaction = PointsTransaction.create(
            recipient_id=user.id,
            giver_id=giver_orm.id,
            amount=actual_delta,
            points_type=dto.points.point_type,
        )

        self.db.add(valuation)
        await self.points_transaction_repository.add(self.db, points_transaction)
        self.db.add(user)
        await self.db.commit()

        return await map_orm_user_to_user_read_dto(user)
