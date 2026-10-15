"""Create, publish and manage community meetings and participant RSVPs."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.config import YEKATERINBURG_TZ, AppSettings
from ..domain.exceptions import DomainError
from ..domain.services.technology_stack import (
    TechnologyStackValidationError,
    normalize_technology_name,
    technology_identity,
)
from ..dto.community_meetings import (
    CommunityMeetingCreateDTO,
    CommunityMeetingDraftDTO,
    CommunityMeetingReadDTO,
    MeetingAudienceMemberDTO,
    MeetingResponseDTO,
)
from ..dto.competence_dto import CompetenceReadDTO
from ..infrastructure.community_meeting_repository import CommunityMeetingRepository
from ..presentation.texts.community_meetings import (
    render_meeting_announcement,
    render_meeting_cancelled,
    render_meeting_date,
    render_meeting_draft,
    render_meeting_reminder,
    render_meeting_rescheduled,
)
from ..services.ports import MeetingDeliveryPort, NotificationBlockedError

_MEETING_TIME_FORMAT_LENGTH = 5


@dataclass(frozen=True, slots=True)
class _PreparedMeeting:
    """Normalized, permission-checked creation values shared by preview and execution."""

    starts_at: datetime
    audience: str
    audience_label: str
    members: tuple[MeetingAudienceMemberDTO, ...]
    recipient_count: int
    publication_topic_name: str | None


class CommunityMeetingService:
    """Enforce meeting permissions and coordinate persistence with delivery adapters."""

    def __init__(
        self,
        db: AsyncSession,
        repository: CommunityMeetingRepository,
        settings: AppSettings,
        delivery: MeetingDeliveryPort,
    ) -> None:
        self._db = db
        self._repository = repository
        self._settings = settings
        self._delivery = delivery

    async def preview_creation(
        self, actor_user_id: int, draft: CommunityMeetingDraftDTO, *, now: datetime | None = None
    ) -> str:
        """Validate permissions, date, audience and destination before approval."""
        prepared = await self._prepare_creation(actor_user_id, draft, now=now)
        return render_meeting_draft(
            draft,
            prepared.starts_at,
            prepared.audience_label,
            prepared.recipient_count,
            prepared.publication_topic_name,
        )

    async def create_meeting(
        self, actor_user_id: int, draft: CommunityMeetingDraftDTO, *, now: datetime | None = None
    ) -> str:
        """Persist the meeting and audience snapshot, then publish the invitation."""
        local_now = self._local_now(now)
        prepared = await self._prepare_creation(actor_user_id, draft, now=local_now)
        invitee_ids = tuple(member.user_id for member in prepared.members) if prepared.audience != "all" else ()
        meeting = await self._repository.create_meeting(
            self._db,
            CommunityMeetingCreateDTO(
                title=draft.title,
                starts_at=prepared.starts_at,
                venue=draft.venue,
                description=draft.description,
                audience=prepared.audience,
                creator_user_id=actor_user_id,
                created_at=local_now.astimezone(UTC),
                invitee_user_ids=invitee_ids,
            ),
        )
        await self._db.commit()
        message = render_meeting_announcement(meeting.title, meeting.starts_at, meeting.venue, meeting.description)

        if meeting.audience == "all":
            target = self._publication_target()
            if target is None:
                raise DomainError(self._missing_publication_text())
            try:
                await self._delivery.send_announcement(target[0], target[1], message, meeting.id)
            except Exception as error:
                logger.warning(
                    "event=community_meeting_publish status=failed error_type={error_type}",
                    error_type=type(error).__name__,
                )
                return (
                    "Встреча создана, анонс не отправлен. Доставлено 0, не удалось 1; "
                    "администратору нужно проверить публикацию."
                )
            return "Встреча создана, анонс опубликован в группе. Доставлено 1, не удалось 0."

        delivered, failed, skipped = await self._deliver_private_invitations(meeting, prepared.members, message)
        return (
            f"Встреча создана. Приглашения: доставлено {delivered}, не удалось {failed}, "
            f"пропущено по настройке {skipped}."
        )

    async def respond(
        self,
        meeting_id: int,
        user_id: int,
        response: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Accept or replace an RSVP from a current or snapshotted audience member."""
        meeting = await self._required_meeting(meeting_id)
        local_now = self._local_now(now)
        if meeting.status != "scheduled" or local_now.astimezone(UTC) >= meeting.starts_at:
            raise DomainError("Встреча уже началась.")
        if not await self._is_allowed_attendee(meeting, user_id):
            raise DomainError(await self._outside_audience_text(meeting.audience))
        changed = await self._repository.save_response(
            self._db,
            meeting_id=meeting_id,
            user_id=user_id,
            response=response,
            now=local_now.astimezone(UTC),
        )
        await self._db.commit()
        if not changed:
            return "Ваш ответ уже учтён."
        return "Отметил: Приду." if response == "going" else "Отметил: Не смогу."

    async def list_upcoming(self, user_id: int, *, now: datetime | None = None) -> tuple[CommunityMeetingReadDTO, ...]:
        """Return only upcoming meetings visible to this participant."""
        local_now = self._local_now(now).astimezone(UTC)
        meetings = await self._repository.find_meetings(self._db, starts_after=local_now)
        visible: list[CommunityMeetingReadDTO] = []
        for meeting in meetings:
            if await self._is_allowed_attendee(meeting, user_id):
                visible.append(meeting)
        return tuple(visible)

    async def get_attendees(self, meeting_id: int, actor_user_id: int) -> tuple[MeetingResponseDTO, ...]:
        """List RSVP responses only to the meeting creator or an administrator."""
        meeting = await self._required_meeting(meeting_id)
        roles = await self._repository.find_user_roles(self._db, actor_user_id)
        if actor_user_id != meeting.creator_user_id and "Admin" not in roles:
            raise DomainError("Список участников доступен только создателю встречи и администратору.")
        return await self._repository.find_going_responses(self._db, meeting_id)

    async def preview_reschedule(
        self, meeting_id: int, actor_user_id: int, meeting_date: date, start_time: str, *, now: datetime | None = None
    ) -> str:
        meeting, starts_at = await self._prepare_reschedule(
            meeting_id, actor_user_id, meeting_date, start_time, now=now
        )
        going = await self._repository.find_going_responses(self._db, meeting_id)
        return (
            f"Перенести встречу «{meeting.title}» на {render_meeting_date(starts_at)}? "
            f"Уведомить участников: {len(going)}."
        )

    async def reschedule(
        self, meeting_id: int, actor_user_id: int, meeting_date: date, start_time: str, *, now: datetime | None = None
    ) -> str:
        meeting, starts_at = await self._prepare_reschedule(
            meeting_id, actor_user_id, meeting_date, start_time, now=now
        )
        rescheduled_at = self._local_now(now).astimezone(UTC)
        attendees = await self._repository.find_going_responses(self._db, meeting_id)
        await self._repository.update_start(
            self._db,
            meeting_id=meeting_id,
            starts_at=starts_at,
            rescheduled_at=rescheduled_at,
        )
        await self._db.commit()
        sent, failed = await self._send_to_response_users(
            attendees, render_meeting_rescheduled(meeting.title, starts_at)
        )
        return f"Встреча перенесена. Уведомления: доставлено {sent}, не удалось {failed}."

    async def preview_cancellation(self, meeting_id: int, actor_user_id: int) -> str:
        meeting = await self._required_meeting(meeting_id)
        await self._require_update_permission(meeting, actor_user_id)
        attendees = await self._repository.find_going_responses(self._db, meeting_id)
        return f"Отменить встречу «{meeting.title}» и уведомить {len(attendees)} участников?"

    async def cancel(self, meeting_id: int, actor_user_id: int) -> str:
        meeting = await self._required_meeting(meeting_id)
        await self._require_update_permission(meeting, actor_user_id)
        attendees = await self._repository.find_going_responses(self._db, meeting_id)
        await self._repository.update_status(self._db, meeting_id=meeting_id, status="cancelled")
        await self._db.commit()
        sent, failed = await self._send_to_response_users(attendees, render_meeting_cancelled(meeting.title))
        return f"Встреча отменена. Уведомления: доставлено {sent}, не удалось {failed}."

    async def process_due_reminders(self, *, now: datetime | None = None) -> int:
        """Claim each due reminder once, deliver it, and finish meetings after three hours."""
        utc_now = self._local_now(now).astimezone(UTC)
        claims = await self._repository.claim_due_reminders(self._db, utc_now)
        await self._repository.finish_elapsed_meetings(self._db, utc_now)
        await self._db.commit()
        sent_count = 0
        for claim in claims:
            recipients = await self._repository.find_going_reminder_recipients(self._db, claim.meeting.id)
            text = render_meeting_reminder(claim.meeting.title, claim.meeting.starts_at, claim.lead)
            for recipient in recipients:
                try:
                    await self._delivery.send_text(recipient.telegram_id, text)
                except NotificationBlockedError:
                    await self._repository.mark_user_blocked(self._db, recipient.user_id)
                except Exception as error:
                    logger.warning(
                        "event=community_meeting_reminder status=failed error_type={error_type}",
                        error_type=type(error).__name__,
                    )
                else:
                    sent_count += 1
        await self._db.commit()
        return sent_count

    async def _prepare_creation(
        self, actor_user_id: int, draft: CommunityMeetingDraftDTO, *, now: datetime | None = None
    ) -> _PreparedMeeting:
        actor_roles = await self._repository.find_user_roles(self._db, actor_user_id)
        self._require_creator_role(actor_roles)
        starts_at = self._parse_start(draft.meeting_date, draft.start_time, now=now)
        audience, label = await self._resolve_audience(actor_user_id, actor_roles, draft)
        audience_members = await self._repository.find_audience_members(self._db, audience) if audience != "all" else ()
        members = tuple(member for member in audience_members if member.user_id != actor_user_id)
        recipient_count = sum(member.invitations_enabled and not member.bot_is_blocked for member in members)
        publication_topic_name = self._publication_topic_name() if audience == "all" else None
        return _PreparedMeeting(starts_at, audience, label, members, recipient_count, publication_topic_name)

    async def _resolve_audience(
        self, actor_user_id: int, actor_roles: frozenset[str], draft: CommunityMeetingDraftDTO
    ) -> tuple[str, str]:
        if draft.audience_kind == "all":
            if "Admin" not in actor_roles:
                raise DomainError("Ментор может создать встречу только для своей компетенции или технологии.")
            if self._publication_target() is None:
                raise DomainError(self._missing_publication_text())
            return "all", "все участники"
        target = draft.audience_target
        if target is None or not target:
            raise DomainError("Уточните компетенцию или технологию аудитории встречи.")
        if draft.audience_kind == "competence":
            competence = await self._resolve_competence(target)
            if "Admin" not in actor_roles:
                owned_competences = await self._repository.find_user_competence_ids(self._db, actor_user_id)
                if competence.id not in owned_competences:
                    raise DomainError("Ментор может создавать встречу только по своей компетенции.")
            return f"competence:{competence.id}", f"участники с компетенцией «{competence.name}»"
        technology = self._normalize_audience_technology(target)
        if "Admin" not in actor_roles:
            known_technologies = await self._repository.find_user_known_technologies(self._db, actor_user_id)
            if not any(self._same_technology(technology, known) for known in known_technologies):
                raise DomainError("Ментор может создавать встречу по технологии, которую сам отметил «знаю».")
        return f"stack:{technology}", f"участники со стеком «{technology}»"

    async def _resolve_competence(self, target: str) -> CompetenceReadDTO:
        target_text = target.strip()
        try:
            target_identity = technology_identity(target_text)
        except TechnologyStackValidationError:
            target_identity = target_text.casefold()
        candidates = []
        for item in await self._repository.find_competences(self._db):
            name_matches = item.name.casefold() == target_text.casefold()
            try:
                alias_matches = technology_identity(item.name) == target_identity
            except TechnologyStackValidationError:
                alias_matches = False
            if name_matches or alias_matches:
                candidates.append(item)
        if not candidates:
            raise DomainError(f"Компетенция «{target_text}» не найдена. Уточните её название из каталога.")
        if len(candidates) != 1:
            names = ", ".join(item.name for item in candidates)
            raise DomainError(f"Название неоднозначно: {names}. Уточните нужную компетенцию.")
        return candidates[0]

    @staticmethod
    def _require_creator_role(actor_roles: frozenset[str]) -> None:
        if not actor_roles.intersection({"Admin", "Mentor"}):
            raise DomainError("Создавать встречи могут только администраторы и менторы.")

    @staticmethod
    def _normalize_audience_technology(target: str) -> str:
        try:
            return normalize_technology_name(target)
        except TechnologyStackValidationError as error:
            raise DomainError("Уточните название технологии для аудитории встречи.") from error

    async def _prepare_reschedule(
        self,
        meeting_id: int,
        actor_user_id: int,
        meeting_date: date,
        start_time: str,
        *,
        now: datetime | None,
    ) -> tuple[CommunityMeetingReadDTO, datetime]:
        meeting = await self._required_meeting(meeting_id)
        await self._require_update_permission(meeting, actor_user_id)
        starts_at = self._parse_start(meeting_date, start_time, now=now)
        return meeting, starts_at

    async def _require_update_permission(self, meeting: CommunityMeetingReadDTO, actor_user_id: int) -> None:
        roles = await self._repository.find_user_roles(self._db, actor_user_id)
        if actor_user_id != meeting.creator_user_id and "Admin" not in roles:
            raise DomainError("Изменить или отменить встречу может её создатель или администратор.")
        if meeting.status != "scheduled":
            raise DomainError("Можно изменить только запланированную встречу.")

    async def _required_meeting(self, meeting_id: int) -> CommunityMeetingReadDTO:
        meeting = await self._repository.find_meeting(self._db, meeting_id)
        if meeting is None:
            raise DomainError("Встреча не найдена.")
        return meeting

    async def _is_allowed_attendee(self, meeting: CommunityMeetingReadDTO, user_id: int) -> bool:
        if meeting.audience == "all":
            return True
        return await self._repository.has_invitee(
            self._db, meeting_id=meeting.id, user_id=user_id
        ) or await self._repository.is_current_audience_member(self._db, meeting_id=meeting.id, user_id=user_id)

    async def _deliver_private_invitations(
        self,
        meeting: CommunityMeetingReadDTO,
        members: tuple[MeetingAudienceMemberDTO, ...],
        message: str,
    ) -> tuple[int, int, int]:
        delivered = 0
        failed = 0
        skipped = 0
        for member in members:
            if not member.invitations_enabled:
                skipped += 1
                continue
            if member.bot_is_blocked:
                failed += 1
                continue
            try:
                await self._delivery.send_invitation(member.telegram_id, message, meeting.id)
            except NotificationBlockedError:
                failed += 1
                await self._repository.mark_user_blocked(self._db, member.user_id)
            except Exception as error:
                failed += 1
                logger.warning(
                    "event=community_meeting_invitation status=failed error_type={error_type}",
                    error_type=type(error).__name__,
                )
            else:
                delivered += 1
                await self._repository.set_invitation_delivered(
                    self._db, meeting_id=meeting.id, user_id=member.user_id, delivered=True
                )
        await self._db.commit()
        return delivered, failed, skipped

    async def _send_to_response_users(self, attendees: tuple[MeetingResponseDTO, ...], message: str) -> tuple[int, int]:
        sent = 0
        failed = 0
        for attendee in attendees:
            recipient = await self._repository.find_telegram_id(self._db, attendee.user_id)
            if recipient is None:
                failed += 1
                continue
            try:
                await self._delivery.send_text(recipient, message)
            except NotificationBlockedError:
                failed += 1
                await self._repository.mark_user_blocked(self._db, attendee.user_id)
            except Exception as error:
                failed += 1
                logger.warning(
                    "event=community_meeting_update status=failed error_type={error_type}",
                    error_type=type(error).__name__,
                )
            else:
                sent += 1
        await self._db.commit()
        return sent, failed

    async def _audience_label(self, audience: str) -> str:
        if audience == "all":
            return "всех участников"
        kind, value = audience.split(":", maxsplit=1)
        if kind == "competence":
            competence = await self._repository.find_competence_by_id(self._db, int(value))
            return f"участников с компетенцией «{competence or value}»"
        return f"участников со стеком «{value}»"

    async def _outside_audience_text(self, audience: str) -> str:
        if audience == "all":
            return "Эта встреча для участников сообщества."
        return f"Эта встреча для {await self._audience_label(audience)}."

    def _publication_target(self) -> tuple[int, int] | None:
        if self._settings.events_chat_id is not None and self._settings.events_topic_id is not None:
            return self._settings.events_chat_id, self._settings.events_topic_id
        if self._settings.agent_group_chat_id is not None and self._settings.agent_topic_id is not None:
            return self._settings.agent_group_chat_id, self._settings.agent_topic_id
        return None

    def _publication_topic_name(self) -> str | None:
        if self._settings.events_chat_id is not None and self._settings.events_topic_id is not None:
            return self._settings.events_topic_name
        if self._settings.agent_group_chat_id is not None and self._settings.agent_topic_id is not None:
            return "тема агента"
        return None

    @staticmethod
    def _missing_publication_text() -> str:
        return (
            "Нельзя создать встречу для всех: не настроена тема для анонсов. "
            "Администратору нужно задать EVENTS_CHAT_ID и EVENTS_TOPIC_ID "
            "или AGENT_GROUP_CHAT_ID и AGENT_TOPIC_ID."
        )

    @staticmethod
    def _parse_start(meeting_date: date, start_time: str, *, now: datetime | None) -> datetime:
        try:
            parsed_time = time.fromisoformat(start_time)
        except ValueError as error:
            raise DomainError("Время укажите строго в формате ЧЧ:ММ.") from error
        if len(start_time) != _MEETING_TIME_FORMAT_LENGTH:
            raise DomainError("Время укажите строго в формате ЧЧ:ММ.")
        local_start = datetime.combine(meeting_date, parsed_time, tzinfo=YEKATERINBURG_TZ)
        local_now = CommunityMeetingService._local_now(now)
        if local_start <= local_now:
            raise DomainError("Нельзя создать или перенести встречу в прошлое.")
        return local_start.astimezone(UTC)

    @staticmethod
    def _same_technology(left: str, right: str) -> bool:
        try:
            return technology_identity(left) == technology_identity(right)
        except TechnologyStackValidationError:
            return left.strip().casefold() == right.strip().casefold()

    @staticmethod
    def _local_now(now: datetime | None) -> datetime:
        current = now or datetime.now(YEKATERINBURG_TZ)
        if current.tzinfo is None:
            return current.replace(tzinfo=YEKATERINBURG_TZ)
        return current.astimezone(YEKATERINBURG_TZ)
