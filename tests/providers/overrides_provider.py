from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup
from dishka import Provider, Scope, provide

from pybot.dto import AttendanceRecognitionResultDTO, NotifyDTO
from pybot.dto.community_checkins import ParsedCheckinResponseDTO
from pybot.services.ports import (
    AttendanceRecognitionPort,
    CheckinResponseParserPort,
    CommunityAchievementDispatchPort,
    MeetingDeliveryPort,
    NotificationPort,
    PulseReportDeliveryPort,
)

__test__ = False


class FakeAttendanceRecognitionPort:
    """Fail loudly if an integration test unexpectedly tries external recognition."""

    async def recognize_attendance_sheet(
        self,
        photo_bytes: bytes,
        mime_type: str,
        candidate_user_names: list[str],
    ) -> AttendanceRecognitionResultDTO:
        raise AssertionError("Photo recognition requires an explicit test result; network calls are forbidden")


class FakeCommunityAchievementDispatchPort(CommunityAchievementDispatchPort):
    async def recalculate_user_achievements(self, user_id: int) -> None:
        return None


class FakeCheckinResponseParserPort(CheckinResponseParserPort):
    """Fail loudly if a test unexpectedly tries to invoke the model-backed parser."""

    async def parse(self, text: str) -> ParsedCheckinResponseDTO:
        del text
        raise AssertionError("Weekly check-in parsing requires an explicit test result; model calls are forbidden")


class FakePulseReportDeliveryPort(PulseReportDeliveryPort):
    """Keep report delivery inside the test process."""

    async def send_pdf(self, *, recipient_id: int, filename: str, content: bytes, caption: str) -> None:
        del recipient_id, filename, content, caption


class FakeMeetingDeliveryPort(MeetingDeliveryPort):
    """Keep meeting notifications inside tests without using Telegram."""

    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        del chat_id, topic_id, text, meeting_id

    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        del telegram_id, text, meeting_id

    async def send_text(self, telegram_id: int, text: str) -> None:
        del telegram_id, text


class FakeNotificationPort(NotificationPort):
    """In-memory notification adapter for isolated tests."""

    def __init__(self) -> None:
        self.role_requests: list[RoleRequestNotificationRecord] = []
        self.direct_messages: list[DirectMessageNotificationRecord] = []

    async def send_role_request_to_admin(self, request_id: int, requester_user_id: int, role_name: str) -> None:
        self.role_requests.append(
            RoleRequestNotificationRecord(
                request_id=request_id,
                requester_user_id=requester_user_id,
                role_name=role_name,
            )
        )

    async def send_message(self, message_data: NotifyDTO) -> None:
        self.direct_messages.append(
            DirectMessageNotificationRecord(
                recipient_id=message_data.recipient_id,
                message_text=message_data.message,
                parse_mode=message_data.parse_mode,
            )
        )

    async def send_attendance_notification(
        self,
        recipient_tg_id: int,
        text: str,
        import_id: int | None = None,
    ) -> None:
        return None

    async def update_role_request_message(
        self,
        request_id: int,
        status_text: str,
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class RoleRequestNotificationRecord:
    request_id: int
    requester_user_id: int
    role_name: str


@dataclass(frozen=True, slots=True)
class DirectMessageNotificationRecord:
    recipient_id: int
    message_text: str
    parse_mode: str | None


@dataclass(frozen=True, slots=True)
class SentMessageRecord:
    chat_id: int
    text: str
    parse_mode: str | None
    reply_markup: InlineKeyboardMarkup | None


class FakeBot:
    """Minimal bot stub to avoid real Telegram API calls in tests."""

    def __init__(self) -> None:
        self.sent_messages: list[SentMessageRecord] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self.sent_messages.append(
            SentMessageRecord(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        )


class TestOverridesProvider(Provider):
    """Dishka provider with fake outbound adapters for tests."""

    def __init__(self) -> None:
        super().__init__()
        self._fake_bot = FakeBot()
        self._notification_port = FakeNotificationPort()
        self._attendance_recognition_port = FakeAttendanceRecognitionPort()

    @provide(scope=Scope.APP)
    def provide_bot(self) -> FakeBot:
        return self._fake_bot

    @provide(scope=Scope.APP)
    def provide_notification_port(self) -> NotificationPort:
        return self._notification_port

    @provide(scope=Scope.APP)
    def provide_attendance_recognition_port(self) -> AttendanceRecognitionPort:
        return self._attendance_recognition_port

    @provide(scope=Scope.APP)
    def provide_community_achievement_dispatch_port(self) -> CommunityAchievementDispatchPort:
        return FakeCommunityAchievementDispatchPort()

    @provide(scope=Scope.APP)
    def provide_checkin_response_parser(self) -> CheckinResponseParserPort:
        return FakeCheckinResponseParserPort()

    @provide(scope=Scope.APP)
    def provide_pulse_report_delivery_port(self) -> PulseReportDeliveryPort:
        return FakePulseReportDeliveryPort()

    @provide(scope=Scope.APP)
    def provide_meeting_delivery_port(self) -> MeetingDeliveryPort:
        return FakeMeetingDeliveryPort()

    @provide(scope=Scope.APP)
    def provide_fake_notification_port(self) -> FakeNotificationPort:
        return self._notification_port
