"""Доменные исключения для слоя Domain (DDD).

Все исключения наследуют от базового DomainError.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from ..core.constants import TaskScheduleKind


class DomainError(Exception):
    """Базовое исключение для всех доменных ошибок."""

    def __init__(self, message: str, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ============ ПОЛЬЗОВАТЕЛЬСКИЕ ОШИБКИ ============


class UserNotFoundError(DomainError):
    """Пользователь не найден в системе."""

    def __init__(self, user_id: int | None = None, telegram_id: int | None = None, phone: str | None = None) -> None:
        details = {}
        if user_id:
            details["user_id"] = user_id
        if telegram_id:
            details["telegram_id"] = telegram_id
        if phone:
            details["phone"] = phone

        msg = "Пользователь не найден"
        if user_id:
            msg += f" (ID: {user_id})"
        elif telegram_id:
            msg += f" (Telegram: {telegram_id})"
        elif phone:
            msg += f" (Phone: {phone})"

        super().__init__(msg, details=details)


class UsersNotFoundError(DomainError):
    """Пользователи не найдены в системе."""

    def __init__(self) -> None:
        super().__init__("Пользователи не найдены в системе")


class UserAlreadyExistsError(DomainError):
    """Пользователь уже существует."""

    def __init__(self, telegram_id: int | None = None, phone: str | None = None) -> None:
        details = {}
        if telegram_id:
            details["telegram_id"] = telegram_id
        if phone:
            details["phone"] = phone

        msg = "Пользователь уже существует в системе"
        super().__init__(msg, details=details)


class InvalidPointsValueError(DomainError):
    """Некорректное значение баллов."""

    def __init__(self, value: int, min_val: int = 0, max_val: int | None = None) -> None:
        details = {"value": value, "min": min_val}
        if max_val:
            details["max"] = max_val

        msg = f"Некорректное значение баллов: {value}. Минимум: {min_val}"
        if max_val:
            msg += f", Максимум: {max_val}"

        super().__init__(msg, details=details)


class ZeroPointsAdjustmentError(DomainError):
    """Попытка начислить/снять 0 баллов."""

    def __init__(self) -> None:
        super().__init__("Нельзя изменить баллы на 0")


class IncompatiblePointsTypeError(DomainError):
    """Несовместимые типы баллов при операции."""

    def __init__(self, type1: str, type2: str) -> None:
        super().__init__(f"Несовместимые типы баллов: {type1} и {type2}", details={"type1": type1, "type2": type2})


class LevelNotFoundError(DomainError):
    """Уровень не найден."""

    def __init__(self, level_id: int | None = None, points_type: str | None = None) -> None:
        details = {}
        if level_id:
            details["level_id"] = level_id
        if points_type:
            details["points_type"] = points_type

        msg = "Уровень не найден"
        super().__init__(msg, details=details)


class InvalidLevelTransitionError(DomainError):
    """Некорректный переход уровня."""

    def __init__(self, current_level_id: int, target_level_id: int, reason: str) -> None:
        super().__init__(
            f"Невозможен переход со уровня {current_level_id} на {target_level_id}: {reason}",
            details={"current": current_level_id, "target": target_level_id, "reason": reason},
        )


class RoleNotFoundError(DomainError):
    """Роль не найдена в системе."""

    def __init__(self, role_name: str) -> None:
        super().__init__(f"Роль '{role_name}' не найдена в системе", details={"role": role_name})


class RoleNotFoundByIdError(DomainError):
    """Роль с указанным ID не найдена в системе."""

    def __init__(self, role_id: int) -> None:
        super().__init__(f"Роль c ID '{role_id}' не найдена в системе", details={"role": role_id})


class RoleAlreadyAssignedError(DomainError):
    """Роль уже назначена пользователю."""

    def __init__(self, user_id: int, role_name: str) -> None:
        super().__init__(
            f"Пользователь {user_id} уже имеет роль '{role_name}'", details={"user_id": user_id, "role": role_name}
        )


class InvalidRoleChangeError(DomainError):
    """Ошибка при изменении роли."""

    def __init__(self, user_id: int, role_name: str, reason: str) -> None:
        super().__init__(
            f"Невозможно изменить роль пользователя {user_id} на '{role_name}': {reason}",
            details={"user_id": user_id, "role": role_name, "reason": reason},
        )


class RoleRequestAlreadyExistsError(DomainError):
    """Запрос на роль уже существует."""

    def __init__(self, user_id: int, role_name: str) -> None:
        super().__init__(
            f"Запрос на роль '{role_name}' уже существует для пользователя {user_id}",
            details={"user_id": user_id, "role": role_name},
        )


class RoleRequestAlreadyProcessedError(DomainError):
    """Заявка на роль уже была обработана (принята или отклонена)."""

    def __init__(self) -> None:
        super().__init__(
            "Заявка на роль уже обработана",
        )


class RoleRequestCooldownError(DomainError):
    """Пользователь пытается подать заявку на роль до истечения кулдауна."""

    def __init__(self, user_id: int, role_name: str, available_at: datetime) -> None:
        self.available_at = available_at
        super().__init__(
            f"Заявка на роль '{role_name}' недоступна до {available_at.isoformat()} для пользователя {user_id}",
            details={"user_id": user_id, "role": role_name, "available_at": available_at.isoformat()},
        )


class RoleRequestNotFoundError(DomainError):
    """Запрошенная заявка на роль не найдена."""

    def __init__(self) -> None:
        super().__init__(
            "Заявка на роль не найдена",
        )


class CommandTargetNotSpecifiedError(DomainError):
    """Цель команды (например, пользователь) не указана, хотя является обязательной."""

    def __init__(self, command_name: str) -> None:
        super().__init__(
            f"Целевой пользователь не указан для команды '{command_name}'",
            details={"command": command_name},
        )


class BroadcastMessageNotSpecifiedError(DomainError):
    """В команде массовой рассылки отсутствует тело сообщения."""

    def __init__(self) -> None:
        super().__init__("Текст сообщения для рассылки не указан")


class UsersRolesNotFoundError(DomainError):
    """Роли пользователей не найдены в системе."""

    def __init__(self) -> None:
        super().__init__("Роли пользователей не найдены в системе")


class InvalidPhoneNumberError(DomainError):
    """Некорректный формат номера телефона."""

    def __init__(self, phone: str, reason: str = "") -> None:
        msg = f"Некорректный номер телефона: {phone}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg, details={"phone": phone})


NameInputValidationReason = Literal["empty", "invalid_symbols", "too_short", "too_long"]


class NameInputValidationError(DomainError, ValueError):
    """Семантическая ошибка валидации name-like ввода."""

    def __init__(
        self,
        reason: NameInputValidationReason,
        *,
        max_length: int | None = None,
    ) -> None:
        self.reason = reason
        self.max_length = max_length
        details: dict[str, Any] = {"reason": reason}
        if max_length is not None:
            details["max_length"] = max_length
        super().__init__("Некорректное значение для имени пользователя", details=details)


class InitialLevelsNotFoundError(DomainError):
    """Начальные уровни не настроены в системе."""

    def __init__(self) -> None:
        super().__init__("В системе не настроены начальные уровни. Пожалуйста, свяжитесь с администратором.")


class DatabaseOperationError(DomainError):
    """Ошибка при работе с БД."""

    def __init__(self, operation: str, entity: str, reason: str | None = None) -> None:
        msg = f"Ошибка при {operation} {entity}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, details={"operation": operation, "entity": entity})


class ValidationError(DomainError):
    """Ошибка валидации данных."""

    def __init__(self, field: str, value: Any, reason: str) -> None:
        super().__init__(f"Ошибка валидации поля '{field}': {reason}", details={"field": field, "value": value})


class PointsValidationError(DomainError):
    """Ошибка валидации баллов."""

    def __init__(self, field: str, value: Any, reason: str) -> None:
        super().__init__(f"Ошибка валидации баллов '{field}': {reason}", details={"field": field, "value": value})


class BroadcastAlreadyRunningError(RuntimeError):
    """Генерируется, если новая массовая рассылка запускается во время работы предыдущей."""


class TaskScheduleError(DomainError, ValueError):
    """Базовое доменное исключение для ошибок инвариантов и доступа к полям TaskSchedule."""


class TaskScheduleFieldTypeError(TaskScheduleError):
    """Указан неверный тип данных в поле расписания задач."""

    def __init__(self, field_name: str, expected_type: str, actual_value: object) -> None:
        actual_type = type(actual_value).__name__
        super().__init__(
            f"{field_name} must be {expected_type}, got {actual_type}",
            details={"field": field_name, "expected_type": expected_type, "actual_type": actual_type},
        )


class TaskScheduleTimezoneAwareRequiredError(TaskScheduleError):
    """В расписании требуется указание объекта времени с часовым поясом."""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"{field_name} must be timezone-aware", details={"field": field_name})


class TaskScheduleIntervalNonPositiveError(TaskScheduleError):
    """Интервал расписания должен быть положительным."""

    def __init__(self, interval: timedelta) -> None:
        super().__init__("interval must be greater than zero", details={"field": "interval", "value": interval})


class TaskScheduleIntervalTooShortError(TaskScheduleError):
    """Интервал расписания слишком короткий (минимальное значение - 1 секунда)."""

    def __init__(self, interval: timedelta) -> None:
        super().__init__("interval must be at least 1 second", details={"field": "interval", "value": interval})


class TaskScheduleMissingFieldError(TaskScheduleError):
    """Пропущено обязательное поле для определенного типа расписания."""

    def __init__(self, kind: TaskScheduleKind, field_name: str) -> None:
        super().__init__(
            f"{kind.value} schedule requires {field_name}",
            details={"kind": kind.value, "field": field_name},
        )


class TaskScheduleUnexpectedFieldsError(TaskScheduleError):
    """В расписании указаны поля, не предназначенные для данного типа расписания."""

    def __init__(self, kind: TaskScheduleKind, field_names: tuple[str, ...]) -> None:
        fields_text = ", ".join(field_names)
        super().__init__(
            f"{kind.value} schedule contains unexpected fields: {fields_text}",
            details={"kind": kind.value, "fields": field_names},
        )


class TaskScheduleFieldUnavailableError(TaskScheduleError):
    """Попытка доступа к полю, недоступному для данного типа расписания задач."""

    def __init__(self, field_name: str, expected_kind: TaskScheduleKind, actual_kind: TaskScheduleKind) -> None:
        super().__init__(
            f"{field_name} is only available for {expected_kind.value.upper()} schedules",
            details={"field": field_name, "expected_kind": expected_kind.value, "actual_kind": actual_kind.value},
        )


class TaskScheduleUnknownKindError(TaskScheduleError):
    """Указан неизвестный тип расписания задач."""

    def __init__(self, kind: Any) -> None:
        super().__init__(f"Unknown schedule kind: {kind}", details={"kind": kind})


class CompetenceNotFoundError(DomainError, ValueError):
    """Компетенция не найдена по предоставленному имени или идентификатору."""

    def __init__(
        self,
        *,
        missing_names: Sequence[str] | None = None,
        missing_ids: Sequence[int] | None = None,
    ) -> None:
        if missing_names is not None:
            super().__init__(
                f"Competence names not found: {list(missing_names)}",
                details={"missing_names": list(missing_names)},
            )
            return

        if missing_ids is not None:
            super().__init__(
                f"Competence ids not found: {list(missing_ids)}",
                details={"missing_ids": list(missing_ids)},
            )
            return

        super().__init__("Competence was not found")


# ============ ОШИБКИ УЧЁТА ПОСЕЩАЕМОСТИ ============


class AttendanceDomainError(DomainError):
    """Базовое исключение для ошибок учёта посещаемости."""

    def __init__(
        self,
        message: str = "Ошибка учёта посещаемости",
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class AttendanceRecognitionError(AttendanceDomainError):
    """Ошибка распознавания листа посещаемости через нейросеть."""

    def __init__(
        self,
        message: str = "Ошибка распознавания фото посещаемости",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ATTENDANCE_RECOGNITION_ERROR", details=details)


class AttendanceNetworkError(AttendanceDomainError):
    """Сетевая ошибка при обращении к API распознавания."""

    def __init__(
        self,
        message: str = "Сетевая ошибка при обращении к сервису распознавания",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ATTENDANCE_NETWORK_ERROR", details=details)


class AttendanceDraftNotFoundError(AttendanceDomainError):
    """Черновик импорта посещаемости не найден."""

    def __init__(
        self,
        message: str = "Черновик импорта посещаемости не найден",
        *,
        import_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if import_id is not None:
            merged_details["import_id"] = import_id
        super().__init__(message, code="ATTENDANCE_DRAFT_NOT_FOUND", details=merged_details)


class AttendanceDraftExpiredError(AttendanceDomainError):
    """Черновик импорта посещаемости истёк."""

    def __init__(
        self,
        message: str = "Срок действия черновика импорта истёк",
        *,
        import_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if import_id is not None:
            merged_details["import_id"] = import_id
        super().__init__(message, code="ATTENDANCE_DRAFT_EXPIRED", details=merged_details)


class AttendanceDateAlreadyProcessedError(AttendanceDomainError):
    """Дата посещаемости уже была обработана ранее."""

    def __init__(
        self,
        message: str = "Посещаемость за эту дату уже была учтена",
        *,
        processed_date: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if processed_date is not None:
            merged_details["processed_date"] = processed_date
        super().__init__(message, code="ATTENDANCE_DATE_ALREADY_PROCESSED", details=merged_details)


class AttendanceDateOutOfRangeError(AttendanceDomainError):
    """Дата посещаемости выходит за допустимые границы."""

    def __init__(
        self,
        message: str = "Дата посещаемости выходит за допустимый диапазон",
        *,
        target_date: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if target_date is not None:
            merged_details["target_date"] = target_date
        super().__init__(message, code="ATTENDANCE_DATE_OUT_OF_RANGE", details=merged_details)


class AttendancePhotoEmptyError(AttendanceDomainError):
    """Фотография пуста или отсутствует."""

    def __init__(
        self,
        message: str = "Файл фотографии пуст или не содержит данных",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ATTENDANCE_PHOTO_EMPTY", details=details)


class AttendanceUserNotFoundError(AttendanceDomainError):
    """Пользователь для строки посещаемости не найден."""

    def __init__(
        self,
        message: str = "Пользователь не найден среди участников клуба",
        *,
        raw_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if raw_name is not None:
            merged_details["raw_name"] = raw_name
        super().__init__(message, code="ATTENDANCE_USER_NOT_FOUND", details=merged_details)


class AttendanceUnauthorizedError(AttendanceDomainError):
    """Действие с учётом посещаемости не авторизовано."""

    def __init__(
        self,
        message: str = "У вас нет прав для работы с учётом посещаемости",
        *,
        user_tg_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if user_tg_id is not None:
            merged_details["user_tg_id"] = user_tg_id
        super().__init__(message, code="ATTENDANCE_UNAUTHORIZED", details=merged_details)


class AttendancePhotoAccessDeniedError(AttendanceDomainError):
    """Пользователь не имеет прав доступа к фотографии импорта."""

    def __init__(
        self,
        message: str = "Фото недоступно: посещение удалено.",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ATTENDANCE_PHOTO_ACCESS_DENIED", details=details)


class AttendancePhotoNotFoundError(AttendanceDomainError):
    """Фотография импорта не найдена или была удалена."""

    def __init__(
        self,
        message: str = "Фотография импорта не найдена.",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="ATTENDANCE_PHOTO_NOT_FOUND", details=details)
