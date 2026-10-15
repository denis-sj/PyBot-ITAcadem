"""PostgreSQL integration coverage for meeting permissions and participant flow."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.config import YEKATERINBURG_TZ, AppSettings
from pybot.db.models import (
    CommunityMeeting,
    CommunityMeetingInvitee,
    CommunityMeetingResponse,
    Competence,
    Role,
    User,
    UserCompetence,
    UserNotificationPreference,
    UserRole,
    UserTechnologyStack,
)
from pybot.domain.exceptions import DomainError
from pybot.infrastructure.community_meeting_repository import CommunityMeetingRepository
from pybot.services.community_meetings import CommunityMeetingService
from pybot.dto.community_meetings import CommunityMeetingDraftDTO, MeetingAudienceKind
from pybot.services.ports import MeetingDeliveryPort, NotificationBlockedError

_CREATED_AT = datetime(2026, 10, 15, 12, 0, tzinfo=YEKATERINBURG_TZ)


class RecordingMeetingDelivery(MeetingDeliveryPort):
    """Record all outbound messages while simulating Telegram blocks when requested."""

    def __init__(self, *, blocked_telegram_ids: frozenset[int] = frozenset()) -> None:
        self.blocked_telegram_ids = blocked_telegram_ids
        self.announcements: list[tuple[int, int, str, int]] = []
        self.invitations: list[tuple[int, str, int]] = []
        self.messages: list[tuple[int, str]] = []

    async def send_announcement(self, chat_id: int, topic_id: int, text: str, meeting_id: int) -> None:
        self.announcements.append((chat_id, topic_id, text, meeting_id))

    async def send_invitation(self, telegram_id: int, text: str, meeting_id: int) -> None:
        self.invitations.append((telegram_id, text, meeting_id))
        self._raise_if_blocked(telegram_id)

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.messages.append((telegram_id, text))
        self._raise_if_blocked(telegram_id)

    def _raise_if_blocked(self, telegram_id: int) -> None:
        if telegram_id in self.blocked_telegram_ids:
            raise NotificationBlockedError("recipient blocked the bot")


async def _create_user(db: AsyncSession, first_name: str, telegram_id: int) -> User:
    user = User(first_name=first_name, last_name="Тестовый", telegram_id=telegram_id)
    db.add(user)
    await db.flush()
    return user


async def _attach_role(db: AsyncSession, user: User, name: str) -> None:
    role = await db.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(name=name, description=name)
        db.add(role)
        await db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    await db.flush()


async def _create_competence(db: AsyncSession, name: str) -> Competence:
    competence = Competence(name=name, description=name)
    db.add(competence)
    await db.flush()
    return competence


async def _attach_competence(db: AsyncSession, user: User, competence: Competence) -> None:
    db.add(UserCompetence(user_id=user.id, competence_id=competence.id))
    await db.flush()


def _service(db: AsyncSession, settings: AppSettings, delivery: RecordingMeetingDelivery) -> CommunityMeetingService:
    return CommunityMeetingService(db, CommunityMeetingRepository(), settings, delivery)


def _draft(
    *,
    audience_kind: MeetingAudienceKind = "all",
    audience_target: str | None = None,
    meeting_date: date = date(2026, 10, 18),
    start_time: str = "14:00",
    title: str = "Разбор Docker Compose",
) -> CommunityMeetingDraftDTO:
    return CommunityMeetingDraftDTO(
        title=title,
        meeting_date=meeting_date,
        start_time=start_time,
        venue="Коворкинг на Ленина, 5",
        audience_kind=audience_kind,
        audience_target=audience_target,
        description="Обсудим практические вопросы.",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_creation_checks_roles_and_preview_matches_persisted_meeting(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    settings_obj.events_chat_id = -1001234567890
    settings_obj.events_topic_id = 61
    settings_obj.events_topic_name = "Встречи"
    delivery = RecordingMeetingDelivery()
    service = _service(db_session, settings_obj, delivery)
    student = await _create_user(db_session, "Студент", 8_885_001)
    mentor = await _create_user(db_session, "Ментор", 8_885_002)
    unrelated_mentor = await _create_user(db_session, "Другой ментор", 8_885_003)
    admin = await _create_user(db_session, "Администратор", 8_885_004)
    competence = await _create_competence(db_session, "Python")
    other_competence = await _create_competence(db_session, "JavaScript")
    await _attach_role(db_session, student, "Student")
    await _attach_role(db_session, mentor, "Mentor")
    await _attach_role(db_session, unrelated_mentor, "Mentor")
    await _attach_role(db_session, admin, "Admin")
    await _attach_competence(db_session, mentor, competence)
    await _attach_competence(db_session, unrelated_mentor, other_competence)
    await db_session.commit()

    with pytest.raises(DomainError, match="только администраторы и менторы"):
        await service.preview_creation(
            student.id, _draft(audience_kind="competence", audience_target="Python"), now=_CREATED_AT
        )
    with pytest.raises(DomainError, match="только по своей компетенции"):
        await service.preview_creation(
            unrelated_mentor.id,
            _draft(audience_kind="competence", audience_target="Python"),
            now=_CREATED_AT,
        )
    with pytest.raises(DomainError, match="только для своей компетенции"):
        await service.preview_creation(mentor.id, _draft(), now=_CREATED_AT)

    draft = _draft(audience_kind="competence", audience_target="PYTHON")
    preview = await service.preview_creation(mentor.id, draft, now=_CREATED_AT)
    assert "Создать встречу «Разбор Docker Compose»?" in preview
    assert "в воскресенье, 18 октября, в 14:00" in preview
    assert "Место: Коворкинг на Ленина, 5" in preview
    assert "Аудитория: участники с компетенцией «Python»" in preview
    assert "Приглашение получат: 0 чел." in preview
    assert await db_session.scalar(select(CommunityMeeting.id)) is None

    all_preview = await service.preview_creation(admin.id, _draft(), now=_CREATED_AT)
    assert "Аудитория: все участники" in all_preview
    assert "Анонс в группе: Встречи" in all_preview
    assert "Приглашение получат:" not in all_preview

    result = await service.create_meeting(mentor.id, draft, now=_CREATED_AT)
    stored = await db_session.scalar(select(CommunityMeeting))
    assert stored is not None
    assert stored.title == "Разбор Docker Compose"
    assert stored.audience == f"competence:{competence.id}"
    assert stored.starts_at == datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    assert result.startswith("Встреча создана. Приглашения:")
    assert delivery.announcements == []

    await service.create_meeting(admin.id, _draft(), now=_CREATED_AT)
    assert delivery.announcements[-1][:2] == (-1001234567890, 61)
    assert "Встреча «Разбор Docker Compose»" in delivery.announcements[-1][2]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_audience_falls_back_to_agent_topic_or_refuses_without_saving(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    admin = await _create_user(db_session, "Администратор", 8_885_010)
    await _attach_role(db_session, admin, "Admin")
    await db_session.commit()
    delivery = RecordingMeetingDelivery()
    settings_obj.events_chat_id = None
    settings_obj.events_topic_id = None
    settings_obj.agent_group_chat_id = None
    settings_obj.agent_topic_id = None
    service = _service(db_session, settings_obj, delivery)

    with pytest.raises(DomainError, match="не настроена тема для анонсов"):
        await service.create_meeting(admin.id, _draft(), now=_CREATED_AT)
    assert await db_session.scalar(select(CommunityMeeting.id)) is None

    settings_obj.agent_group_chat_id = -1001234567891
    settings_obj.agent_topic_id = 19
    preview = await service.preview_creation(admin.id, _draft(), now=_CREATED_AT)
    assert "Анонс в группе: тема агента" in preview
    assert "Приглашение получат:" not in preview
    result = await service.create_meeting(admin.id, _draft(), now=_CREATED_AT)
    assert result == "Встреча создана, анонс опубликован в группе. Доставлено 1, не удалось 0."
    assert delivery.announcements[-1][:2] == (-1001234567891, 19)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stack_audience_matches_technology_aliases_and_snapshots_delivered_invites(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    mentor = await _create_user(db_session, "Ментор", 8_885_020)
    participant = await _create_user(db_session, "Участник", 8_885_021)
    opted_out = await _create_user(db_session, "Без приглашений", 8_885_022)
    invited_participant = await _create_user(db_session, "Получил приглашение", 8_885_023)
    await _attach_role(db_session, mentor, "Mentor")
    db_session.add_all(
        [
            UserTechnologyStack(user_id=mentor.id, technology="PostgreSQL", level="know"),
            UserTechnologyStack(user_id=participant.id, technology="постгрес", level="know"),
            UserTechnologyStack(user_id=opted_out.id, technology="PostgreSQL", level="learning"),
            UserTechnologyStack(user_id=invited_participant.id, technology="PostgreSQL", level="know"),
            UserNotificationPreference(user_id=opted_out.id, meeting_invitations_enabled=False),
        ]
    )
    await db_session.commit()
    delivery = RecordingMeetingDelivery(blocked_telegram_ids=frozenset({participant.telegram_id}))
    service = _service(db_session, settings_obj, delivery)

    stack_preview = await service.preview_creation(
        mentor.id,
        _draft(audience_kind="stack", audience_target="постгрес"),
        now=_CREATED_AT,
    )
    assert "Аудитория: участники со стеком «PostgreSQL»" in stack_preview
    assert "Приглашение получат: 2 чел." in stack_preview

    result = await service.create_meeting(
        mentor.id,
        _draft(audience_kind="stack", audience_target="постгрес"),
        now=_CREATED_AT,
    )
    meeting = await db_session.scalar(select(CommunityMeeting))
    assert meeting is not None and meeting.audience == "stack:PostgreSQL"
    assert "доставлено 1, не удалось 1, пропущено по настройке 1" in result
    invitees = (
        await db_session.scalars(
            select(CommunityMeetingInvitee).where(CommunityMeetingInvitee.meeting_id == meeting.id)
        )
    ).all()
    assert {invitee.user_id for invitee in invitees} == {participant.id, opted_out.id, invited_participant.id}
    assert {invitee.user_id for invitee in invitees if invitee.invitation_delivered} == {invited_participant.id}
    participant_record = await db_session.get(User, participant.id)
    assert participant_record is not None and participant_record.bot_is_blocked is True
    assert {item[0] for item in delivery.invitations} == {participant.telegram_id, invited_participant.telegram_id}

    # A user who no longer matches may answer only if the private invitation was delivered.
    await db_session.execute(delete(UserTechnologyStack).where(UserTechnologyStack.user_id == invited_participant.id))
    await db_session.commit()
    assert (
        await service.respond(meeting.id, invited_participant.id, "going", now=_CREATED_AT + timedelta(minutes=1))
        == "Отметил: Приду."
    )
    # The initial blocked/opted-out recipients are not grandfathered into RSVP access.
    with pytest.raises(DomainError, match="Эта встреча для участников со стеком"):
        await service.respond(meeting.id, opted_out.id, "going", now=_CREATED_AT + timedelta(minutes=1))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rsvp_authorization_changes_idempotence_and_start_boundary(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    settings_obj.events_chat_id = -1001234567890
    settings_obj.events_topic_id = 61
    admin = await _create_user(db_session, "Администратор", 8_885_030)
    attendee = await _create_user(db_session, "Участник", 8_885_031)
    outsider = await _create_user(db_session, "Посторонний", 8_885_032)
    await _attach_role(db_session, admin, "Admin")
    await _attach_role(db_session, attendee, "Student")
    await _attach_role(db_session, outsider, "Student")
    competence = await _create_competence(db_session, "Python")
    await _attach_competence(db_session, attendee, competence)
    await db_session.commit()
    service = _service(db_session, settings_obj, RecordingMeetingDelivery())
    result = await service.create_meeting(
        admin.id,
        _draft(audience_kind="competence", audience_target="Python"),
        now=_CREATED_AT,
    )
    assert "доставлено 1" in result
    meeting = await db_session.scalar(select(CommunityMeeting))
    assert meeting is not None

    await service.respond(meeting.id, attendee.id, "going", now=_CREATED_AT + timedelta(minutes=1))
    assert len(await service.get_attendees(meeting.id, admin.id)) == 1
    assert await service.respond(meeting.id, attendee.id, "going", now=_CREATED_AT + timedelta(minutes=2)) == (
        "Ваш ответ уже учтён."
    )
    await service.respond(meeting.id, attendee.id, "not_going", now=_CREATED_AT + timedelta(minutes=3))
    responses = (
        await db_session.scalars(
            select(CommunityMeetingResponse).where(CommunityMeetingResponse.meeting_id == meeting.id)
        )
    ).all()
    assert len(responses) == 1 and responses[0].response == "not_going"

    with pytest.raises(DomainError, match="Эта встреча для участников с компетенцией «Python»"):
        await service.respond(meeting.id, outsider.id, "going", now=_CREATED_AT + timedelta(minutes=4))
    with pytest.raises(DomainError, match="Встреча уже началась"):
        await service.respond(meeting.id, attendee.id, "going", now=meeting.starts_at)

    assert len(await service.list_upcoming(attendee.id, now=_CREATED_AT)) == 1
    assert await service.list_upcoming(outsider.id, now=_CREATED_AT) == ()
    with pytest.raises(DomainError, match="только создателю встречи"):
        await service.get_attendees(meeting.id, outsider.id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reminders_are_claimed_once_and_skip_day_reminder_for_late_creation(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    settings_obj.events_chat_id = -1001234567890
    settings_obj.events_topic_id = 61
    admin = await _create_user(db_session, "Администратор", 8_885_040)
    attendee = await _create_user(db_session, "Участник", 8_885_041)
    await _attach_role(db_session, admin, "Admin")
    await db_session.commit()
    delivery = RecordingMeetingDelivery()
    service = _service(db_session, settings_obj, delivery)

    first_start = _CREATED_AT + timedelta(hours=24)
    first_date = first_start.date()
    first_draft = _draft(meeting_date=first_date, start_time=f"{first_start.hour:02}:00")
    await service.create_meeting(admin.id, first_draft, now=_CREATED_AT)
    first_meeting = await db_session.scalar(select(CommunityMeeting))
    assert first_meeting is not None
    await service.respond(first_meeting.id, attendee.id, "going", now=_CREATED_AT)

    assert await service.process_due_reminders(now=_CREATED_AT) == 1
    assert await service.process_due_reminders(now=_CREATED_AT) == 0
    assert len(delivery.messages) == 1 and "через 24 часа" in delivery.messages[0][1]

    one_hour_before = first_start - timedelta(hours=1)
    assert await service.process_due_reminders(now=one_hour_before) == 1
    assert await service.process_due_reminders(now=one_hour_before) == 0
    assert len(delivery.messages) == 2 and "через 1 час" in delivery.messages[1][1]

    late_created = _CREATED_AT + timedelta(days=2)
    late_start = late_created + timedelta(hours=23)
    late_draft = _draft(meeting_date=late_start.date(), start_time=f"{late_start.hour:02}:00", title="Поздняя встреча")
    await service.create_meeting(admin.id, late_draft, now=late_created)
    late_meeting = await db_session.scalar(select(CommunityMeeting).where(CommunityMeeting.title == "Поздняя встреча"))
    assert late_meeting is not None
    await service.respond(late_meeting.id, attendee.id, "going", now=late_created)
    assert await service.process_due_reminders(now=late_created) == 0
    assert len(delivery.messages) == 2
    assert await service.process_due_reminders(now=late_start - timedelta(hours=1)) == 1
    assert len(delivery.messages) == 3 and "Поздняя встреча" in delivery.messages[-1][1]

    assert await service.process_due_reminders(now=first_start + timedelta(hours=3)) == 0
    await db_session.refresh(first_meeting)
    assert first_meeting.status == "finished"
    assert len(delivery.messages) == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reschedule_resets_reminders_and_cancel_notifies_going_attendees(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    settings_obj.events_chat_id = -1001234567890
    settings_obj.events_topic_id = 61
    admin = await _create_user(db_session, "Администратор", 8_885_050)
    attendee = await _create_user(db_session, "Участник", 8_885_051)
    await _attach_role(db_session, admin, "Admin")
    await db_session.commit()
    delivery = RecordingMeetingDelivery()
    service = _service(db_session, settings_obj, delivery)
    await service.create_meeting(admin.id, _draft(), now=_CREATED_AT)
    meeting = await db_session.scalar(select(CommunityMeeting))
    assert meeting is not None
    await service.respond(meeting.id, attendee.id, "going", now=_CREATED_AT)
    meeting.reminder_24h_sent = True
    meeting.reminder_1h_sent = True
    await db_session.commit()

    preview = await service.preview_reschedule(meeting.id, admin.id, date(2026, 10, 19), "16:30", now=_CREATED_AT)
    assert "Уведомить участников: 1" in preview
    result = await service.reschedule(meeting.id, admin.id, date(2026, 10, 19), "16:30", now=_CREATED_AT)
    assert result == "Встреча перенесена. Уведомления: доставлено 1, не удалось 0."
    assert "19 октября, в 16:30" in delivery.messages[-1][1]
    await db_session.refresh(meeting)
    assert meeting.reminder_24h_sent is False and meeting.reminder_1h_sent is False

    result = await service.cancel(meeting.id, admin.id)
    assert result == "Встреча отменена. Уведомления: доставлено 1, не удалось 0."
    assert delivery.messages[-1][1] == "Встреча «Разбор Docker Compose» отменена."
    await db_session.refresh(meeting)
    assert meeting.status == "cancelled"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_past_meeting_and_lost_mentor_role_are_rejected(
    db_session: AsyncSession, settings_obj: AppSettings
) -> None:
    mentor = await _create_user(db_session, "Ментор", 8_885_060)
    await _attach_role(db_session, mentor, "Mentor")
    competence = await _create_competence(db_session, "Python")
    await _attach_competence(db_session, mentor, competence)
    await db_session.commit()
    service = _service(db_session, settings_obj, RecordingMeetingDelivery())

    with pytest.raises(DomainError, match="в прошлое"):
        await service.preview_creation(
            mentor.id,
            _draft(
                audience_kind="competence",
                audience_target="Python",
                meeting_date=date(2026, 10, 14),
                start_time="10:00",
            ),
            now=_CREATED_AT,
        )
    mentor_role = await db_session.scalar(select(Role).where(Role.name == "Mentor"))
    assert mentor_role is not None
    await db_session.execute(delete(UserRole).where(UserRole.user_id == mentor.id, UserRole.role_id == mentor_role.id))
    await db_session.commit()
    with pytest.raises(DomainError, match="только администраторы и менторы"):
        await service.preview_creation(
            mentor.id,
            _draft(audience_kind="competence", audience_target="Python"),
            now=_CREATED_AT,
        )
