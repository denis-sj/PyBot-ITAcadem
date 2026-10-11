"""Agent-facing action orchestration over existing application services."""

from datetime import date, datetime, time

from ...core import logger
from ...core.config import YEKATERINBURG_TZ
from ...core.constants import PointsTypeEnum, RoleEnum, TaskScheduleKind
from ...domain.exceptions import (
    AttendanceDomainError,
    DomainError,
    InsufficientPointsBalanceError,
    RoleAlreadyAssignedError,
    RoleNotFoundError,
    UserNotFoundError,
    ZeroPointsAdjustmentError,
)
from ...dto import AdjustUserPointsDTO, NotifyUserDTO, UserReadDTO
from ...dto.attendance import AttendanceRecordReadDTO
from ...dto.role_dto import CreateRoleRequestDTO
from ...dto.value_objects import Points
from ..attendance_service import AttendanceService
from ..competence import CompetenceService
from ..notification_facade import NotificationFacade
from ..points import PointsService
from ..role_request import RoleRequestService
from ..user_services.user_competence import UserCompetenceService
from ..user_services.user_roles import UserRolesService
from ..user_services.users import UserService


class AgentActionService:
    """Run explicitly requested agent actions through established use cases."""

    def __init__(  # noqa: PLR0913
        self,
        role_requests: RoleRequestService,
        users: UserService,
        points: PointsService,
        roles: UserRolesService,
        user_competences: UserCompetenceService,
        competences: CompetenceService,
        attendance: AttendanceService,
        notifications: NotificationFacade,
    ) -> None:
        self.role_requests = role_requests
        self.users = users
        self.points = points
        self.roles = roles
        self.user_competences = user_competences
        self.competences = competences
        self.attendance = attendance
        self.notifications = notifications

    async def validate_role_request(self, user_id: int) -> None:
        """Check current eligibility before asking the user to confirm a role request."""
        await self.role_requests.check_requesting_user(user_id, RoleEnum.MENTOR.value)

    async def request_mentor_role(self, user_id: int) -> CreateRoleRequestDTO:
        """Create the role request after approval; the application service revalidates eligibility."""
        return await self.role_requests.create_role_request(user_id, RoleEnum.MENTOR.value)

    async def find_user(self, telegram_id: int) -> UserReadDTO:
        """Find one registered user by an exact Telegram identity."""
        user = await self.users.find_user_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotFoundError(telegram_id=telegram_id)
        return user

    async def validate_points_adjustment(
        self,
        giver_id: int,
        telegram_id: int,
        points_type: PointsTypeEnum,
        delta: int,
    ) -> None:
        """Check recipient, giver, non-zero delta and current balance before confirmation."""
        recipient = await self.find_user(telegram_id)
        await self.users.get_user(giver_id)
        if delta == 0:
            raise ZeroPointsAdjustmentError
        balance = recipient.academic_points if points_type == PointsTypeEnum.ACADEMIC else recipient.reputation_points
        if delta < 0 and abs(delta) > balance.value:
            raise InsufficientPointsBalanceError(requested=abs(delta), current=balance.value)

    async def adjust_points(
        self,
        giver_id: int,
        telegram_id: int,
        points_type: PointsTypeEnum,
        delta: int,
        reason: str | None,
    ) -> str:
        """Adjust points and notify the recipient using the command's existing workflow."""
        await self.validate_points_adjustment(giver_id, telegram_id, points_type, delta)
        giver = await self.users.get_user(giver_id)
        recipient = await self.find_user(telegram_id)
        points = Points(value=delta, point_type=points_type)
        updated = await self.points.change_points(
            AdjustUserPointsDTO(recipient_id=recipient.id, giver_id=giver.id, points=points, reason=reason)
        )
        action = "начислил" if delta > 0 else "снял"
        points_label = "академических" if points_type == PointsTypeEnum.ACADEMIC else "репутационных"
        message = f"📈 Пользователь {giver.first_name} {action} вам {abs(delta)} {points_label} баллов."
        if reason:
            message += f" Причина: {reason}."
        try:
            await self.notifications.notify_user(
                NotifyUserDTO(
                    recipient_id=recipient.telegram_id,
                    message=message,
                    kind=TaskScheduleKind.IMMEDIATE,
                )
            )
        except Exception as error:
            logger.warning(
                "event=agent_action_notification_failed error_type={error_type}", error_type=type(error).__name__
            )

        verb = "начислено" if delta > 0 else "списано"
        return f"Участнику {self._full_name(updated)} {verb} {abs(delta)} баллов."

    async def validate_role_change(self, telegram_id: int, role: RoleEnum, add: bool) -> None:
        """Check recipient and role state before confirming a role change."""
        user = await self.find_user(telegram_id)
        available_roles = {item.name for item in await self.roles.find_all_roles()}
        if role.value not in available_roles:
            raise RoleNotFoundError(role.value)
        has_role = await self.roles.check_user_role(user.id, role.value)
        if add and has_role:
            raise RoleAlreadyAssignedError(user.id, role.value)
        if not add and not has_role:
            raise DomainError("У участника нет этой роли.")

    async def change_role(self, telegram_id: int, role: RoleEnum, add: bool) -> str:
        """Add or remove one role through the existing role service."""
        await self.validate_role_change(telegram_id, role, add)
        if add:
            updated = await self.roles.add_user_role(telegram_id, role)
            action = "выдана"
        else:
            updated = await self.roles.remove_user_role(telegram_id, role.value)
            action = "снята"
        return f"Роль {role.value} {action} у участника {self._full_name(updated)}."

    async def validate_competence_change(self, telegram_id: int, competence_name: str, add: bool) -> None:
        """Check target and competence membership before confirmation."""
        user = await self.find_user(telegram_id)
        catalog = await self.competences.find_all_competencies()
        selected = next((item for item in catalog if item.name.casefold() == competence_name.casefold()), None)
        if selected is None:
            raise DomainError("Такой компетенции нет в каталоге.")
        current = await self.user_competences.find_user_competencies(user.id)
        has_competence = any(item.id == selected.id for item in current)
        if add and has_competence:
            raise DomainError("Эта компетенция уже назначена участнику.")
        if not add and not has_competence:
            raise DomainError("У участника нет этой компетенции.")

    async def change_competence(self, telegram_id: int, competence_name: str, add: bool) -> str:
        """Add or remove one catalog competence through the existing user competence service."""
        await self.validate_competence_change(telegram_id, competence_name, add)
        user = await self.find_user(telegram_id)
        if add:
            await self.user_competences.add_user_competencies_by_names(user.id, (competence_name,))
            action = "выдана"
        else:
            await self.user_competences.remove_user_competencies_by_names(user.id, (competence_name,))
            action = "снята"
        return f"Компетенция {competence_name} {action} у участника {self._full_name(user)}."

    async def validate_visit_update(
        self,
        telegram_id: int,
        visit_date: date,
        start_time: time,
        end_time: time,
    ) -> None:
        """Check a stored visit and proposed time range before confirmation."""
        _, record = await self._find_visit(telegram_id, visit_date)
        if end_time <= start_time:
            raise AttendanceDomainError("Время окончания должно быть позже начала.")
        if record.start_time == start_time and record.end_time == end_time:
            raise AttendanceDomainError("Новое время совпадает с текущим.")

    async def update_visit_time(
        self,
        telegram_id: int,
        visit_date: date,
        start_time: time,
        end_time: time,
        admin: UserReadDTO,
    ) -> str:
        """Update an existing visit through the audited attendance service."""
        await self.validate_visit_update(telegram_id, visit_date, start_time, end_time)
        _, record = await self._find_visit(telegram_id, visit_date)
        await self.attendance.edit_visit_time(
            record_id=record.id,
            new_start_time=start_time,
            new_end_time=end_time,
            admin_tg_id=admin.telegram_id,
            admin_name=self._full_name(admin),
        )
        return f"Посещение {visit_date.isoformat()} изменено на {start_time:%H:%M}–{end_time:%H:%M}."

    async def validate_visit_delete(self, telegram_id: int, visit_date: date) -> None:
        """Require an existing visit before asking for deletion approval."""
        await self._find_visit(telegram_id, visit_date)

    async def delete_visit(self, telegram_id: int, visit_date: date, admin: UserReadDTO) -> str:
        """Delete an existing visit through the audited attendance service."""
        await self.validate_visit_delete(telegram_id, visit_date)
        _, record = await self._find_visit(telegram_id, visit_date)
        await self.attendance.delete_visit(
            record_id=record.id,
            admin_tg_id=admin.telegram_id,
            admin_name=self._full_name(admin),
        )
        return f"Посещение {visit_date.isoformat()} удалено."

    async def validate_manual_visit(
        self,
        telegram_id: int,
        visit_date: date,
        start_time: time,
        end_time: time,
    ) -> None:
        """Check recipient, date, uniqueness and time before confirming a manual visit."""
        user = await self.find_user(telegram_id)
        today = datetime.now(YEKATERINBURG_TZ).date()
        if visit_date > today:
            raise AttendanceDomainError("Нельзя добавить посещение в будущую дату.")
        if await self.attendance.has_user_visit_on_date(user.id, visit_date):
            raise AttendanceDomainError("У участника уже есть посещение на эту дату.")
        if end_time <= start_time:
            raise AttendanceDomainError("Время окончания должно быть позже начала.")

    async def create_manual_visit(
        self,
        telegram_id: int,
        visit_date: date,
        start_time: time,
        end_time: time,
        admin: UserReadDTO,
    ) -> str:
        """Create a visit through the existing audited manual attendance service."""
        await self.validate_manual_visit(telegram_id, visit_date, start_time, end_time)
        user = await self.find_user(telegram_id)
        await self.attendance.create_manual_visit(
            user_id=user.id,
            visit_date=visit_date,
            start_time=start_time,
            end_time=end_time,
            admin_tg_id=admin.telegram_id,
            admin_name=self._full_name(admin),
        )
        return f"Посещение {visit_date.isoformat()} добавлено: {start_time:%H:%M}–{end_time:%H:%M}."

    async def _find_visit(self, telegram_id: int, visit_date: date) -> tuple[UserReadDTO, AttendanceRecordReadDTO]:
        user = await self.find_user(telegram_id)
        records = await self.attendance.get_user_monthly_visits(user.id, year=visit_date.year, month=visit_date.month)
        record = next((item for item in records if item.date == visit_date), None)
        if record is None:
            raise AttendanceDomainError("За эту дату не найдено посещение участника.")
        return user, record

    @staticmethod
    def _full_name(user: UserReadDTO) -> str:
        return " ".join(part for part in (user.last_name, user.first_name, user.patronymic) if part)
