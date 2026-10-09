from abc import ABC, abstractmethod

from ...dto import NotifyDTO


class NotificationPort(ABC):
    """Контракт исходящих уведомлений для сервисов приложения.

    Notes:
        Семантика `recipient_id` зависит от транспорта. В адаптерах Telegram
        это соответствует `telegram_id` / `chat_id`.
    """

    @abstractmethod
    async def send_role_request_to_admin(
        self,
        request_id: int,
        requester_user_id: int,
        role_name: str,
    ) -> None:
        """Отправляет уведомление администратору о запросе роли.

        Args:
            request_id: Уникальный идентификатор запроса роли.
            requester_user_id: Идентификатор запрашивающего пользователя.
            role_name: Запрошенная роль.

        Raises:
            NotificationTemporaryError: Временная ошибка доставки, возможен повтор.
            NotificationPermanentError: Неустранимая ошибка доставки.
        """
        pass

    @abstractmethod
    async def send_message(self, message_data: NotifyDTO) -> None:
        """Отправляет прямое сообщение одному получателю.

        Args:
            message_data: Валидированный DTO с данными сообщения и получателем.

        Raises:
            NotificationTemporaryError: Временная ошибка доставки, возможен повтор.
            NotificationPermanentError: Неустранимая ошибка доставки.
        """

    @abstractmethod
    async def send_attendance_notification(
        self,
        recipient_tg_id: int,
        text: str,
        import_id: int | None = None,
    ) -> None:
        """Отправляет участнику уведомление о посещениях с опциональной кнопкой фото импорта.

        Args:
            recipient_tg_id: Telegram ID получателя.
            text: Текст уведомления в HTML.
            import_id: Опциональный идентификатор импорта для кнопки фото.

        Raises:
            NotificationTemporaryError: Временная ошибка доставки, возможен повтор.
            NotificationPermanentError: Неустранимая ошибка доставки.
        """
        pass

    @abstractmethod
    async def update_role_request_message(
        self,
        request_id: int,
        status_text: str,
    ) -> None:
        """Обновляет сообщение администратора о запросе роли и убирает кнопки.

        Args:
            request_id: Идентификатор запроса роли.
            status_text: Текст обновлённого статуса.
        """
        pass
