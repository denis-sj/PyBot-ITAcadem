"""Persistence queries for community meetings and their attendance snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
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
from ..domain.services.technology_stack import TechnologyStackValidationError, technology_identity
from ..dto.community_meetings import (
    CommunityMeetingCreateDTO,
    CommunityMeetingReadDTO,
    MeetingAudienceMemberDTO,
    MeetingReminderRecipientDTO,
    MeetingResponseDTO,
)
from ..dto.competence_dto import CompetenceReadDTO

_REMINDER_24_HOURS = 24
_REMINDER_DELIVERY_GRACE = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class MeetingReminderClaim:
    """One meeting reminder flag claimed by a worker before message delivery."""

    meeting: CommunityMeetingReadDTO
    created_at: datetime
    lead: str


class CommunityMeetingRepository:
    """Database access for meeting lifecycle, audience snapshots and responses."""

    async def find_user_roles(self, db: AsyncSession, user_id: int) -> frozenset[str]:
        statement = select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        return frozenset((await db.scalars(statement)).all())

    async def find_user_competence_ids(self, db: AsyncSession, user_id: int) -> frozenset[int]:
        statement = select(UserCompetence.competence_id).where(UserCompetence.user_id == user_id)
        return frozenset((await db.scalars(statement)).all())

    async def find_user_known_technologies(self, db: AsyncSession, user_id: int) -> tuple[str, ...]:
        statement = select(UserTechnologyStack.technology).where(
            UserTechnologyStack.user_id == user_id,
            UserTechnologyStack.level == "know",
        )
        return tuple((await db.scalars(statement)).all())

    async def find_competences(self, db: AsyncSession) -> Sequence[CompetenceReadDTO]:
        rows = (await db.execute(select(Competence.id, Competence.name).order_by(Competence.name))).all()
        return tuple(CompetenceReadDTO(id=competence_id, name=name) for competence_id, name in rows)

    async def find_competence_by_id(self, db: AsyncSession, competence_id: int) -> str | None:
        return await db.scalar(select(Competence.name).where(Competence.id == competence_id))

    async def find_audience_members(self, db: AsyncSession, audience: str) -> Sequence[MeetingAudienceMemberDTO]:
        statement = self._audience_select(audience)
        if statement is None:
            return ()
        result = await db.execute(statement.order_by(User.id))
        members = tuple(
            MeetingAudienceMemberDTO(
                user_id=row.user_id,
                telegram_id=row.telegram_id,
                bot_is_blocked=row.bot_is_blocked,
                invitations_enabled=row.invitations_enabled,
            )
            for row in result.all()
        )
        if not audience.startswith("stack:"):
            return members
        technology = audience.split(":", maxsplit=1)[1]
        identities = await self._find_stack_identities(db, tuple(member.user_id for member in members))
        try:
            target_identity = technology_identity(technology)
        except TechnologyStackValidationError:
            target_identity = technology.casefold()
        return tuple(
            member
            for member in members
            if any(identity == target_identity for identity in identities.get(member.user_id, ()))
        )

    async def count_users(self, db: AsyncSession) -> int:
        return int(await db.scalar(select(func.count()).select_from(User)) or 0)

    async def create_meeting(self, db: AsyncSession, command: CommunityMeetingCreateDTO) -> CommunityMeetingReadDTO:
        meeting = CommunityMeeting(
            title=command.title,
            starts_at=command.starts_at,
            venue=command.venue,
            description=command.description,
            audience=command.audience,
            creator_user_id=command.creator_user_id,
            created_at=command.created_at,
        )
        db.add(meeting)
        await db.flush()
        if command.invitee_user_ids:
            db.add_all(
                CommunityMeetingInvitee(meeting_id=meeting.id, user_id=user_id) for user_id in command.invitee_user_ids
            )
            await db.flush()
        return self._to_dto(meeting)

    async def set_invitation_delivered(
        self, db: AsyncSession, *, meeting_id: int, user_id: int, delivered: bool
    ) -> None:
        await db.execute(
            update(CommunityMeetingInvitee)
            .where(CommunityMeetingInvitee.meeting_id == meeting_id, CommunityMeetingInvitee.user_id == user_id)
            .values(invitation_delivered=delivered)
        )

    async def mark_user_blocked(self, db: AsyncSession, user_id: int) -> None:
        await db.execute(update(User).where(User.id == user_id).values(bot_is_blocked=True))

    async def find_meeting(self, db: AsyncSession, meeting_id: int) -> CommunityMeetingReadDTO | None:
        meeting = await db.get(CommunityMeeting, meeting_id)
        return self._to_dto(meeting) if meeting is not None else None

    async def find_telegram_id(self, db: AsyncSession, user_id: int) -> int | None:
        return await db.scalar(select(User.telegram_id).where(User.id == user_id))

    async def find_meetings(self, db: AsyncSession, *, starts_after: datetime) -> Sequence[CommunityMeetingReadDTO]:
        statement = (
            select(CommunityMeeting)
            .where(CommunityMeeting.status == "scheduled", CommunityMeeting.starts_at > starts_after)
            .order_by(CommunityMeeting.starts_at)
        )
        return tuple(self._to_dto(row) for row in (await db.scalars(statement)).all())

    async def has_invitee(self, db: AsyncSession, *, meeting_id: int, user_id: int) -> bool:
        statement = select(CommunityMeetingInvitee.id).where(
            CommunityMeetingInvitee.meeting_id == meeting_id,
            CommunityMeetingInvitee.user_id == user_id,
            CommunityMeetingInvitee.invitation_delivered.is_(True),
        )
        return await db.scalar(statement) is not None

    async def is_current_audience_member(self, db: AsyncSession, *, meeting_id: int, user_id: int) -> bool:
        meeting = await db.get(CommunityMeeting, meeting_id)
        if meeting is None:
            return False
        statement = self._audience_select(meeting.audience, user_id=user_id)
        if statement is None:
            return True
        if meeting.audience.startswith("stack:"):
            technology = meeting.audience.split(":", maxsplit=1)[1]
            identities = await self._find_stack_identities(db, (user_id,))
            try:
                target_identity = technology_identity(technology)
            except TechnologyStackValidationError:
                target_identity = technology.casefold()
            return target_identity in identities.get(user_id, ())
        return await db.scalar(statement.with_only_columns(User.id).limit(1)) is not None

    async def save_response(
        self, db: AsyncSession, *, meeting_id: int, user_id: int, response: str, now: datetime
    ) -> bool:
        row = CommunityMeetingResponse
        statement = (
            insert(row)
            .values(meeting_id=meeting_id, user_id=user_id, response=response, updated_at=now)
            .on_conflict_do_update(
                index_elements=[row.meeting_id, row.user_id],
                set_={"response": response, "updated_at": now},
                where=row.response != response,
            )
            .returning(row.id)
        )
        return await db.scalar(statement) is not None

    async def update_start(
        self, db: AsyncSession, *, meeting_id: int, starts_at: datetime, rescheduled_at: datetime
    ) -> None:
        await db.execute(
            update(CommunityMeeting)
            .where(CommunityMeeting.id == meeting_id)
            .values(
                starts_at=starts_at,
                rescheduled_at=rescheduled_at,
                reminder_24h_sent=False,
                reminder_1h_sent=False,
            )
        )

    async def update_status(self, db: AsyncSession, *, meeting_id: int, status: str) -> None:
        await db.execute(update(CommunityMeeting).where(CommunityMeeting.id == meeting_id).values(status=status))

    async def find_going_responses(self, db: AsyncSession, meeting_id: int) -> tuple[MeetingResponseDTO, ...]:
        statement = (
            select(
                User.id,
                User.first_name,
                User.last_name,
                User.telegram_username,
                CommunityMeetingResponse.response,
            )
            .join(CommunityMeetingResponse, CommunityMeetingResponse.user_id == User.id)
            .where(CommunityMeetingResponse.meeting_id == meeting_id, CommunityMeetingResponse.response == "going")
            .order_by(func.lower(User.last_name), func.lower(User.first_name), User.id)
        )
        rows = (await db.execute(statement)).all()
        return tuple(
            MeetingResponseDTO(
                user_id=user_id,
                display_name=(
                    f"@{username}" if username else " ".join(part for part in (first_name, last_name) if part)
                ),
                response=response,
            )
            for user_id, first_name, last_name, username, response in rows
        )

    async def find_going_reminder_recipients(
        self, db: AsyncSession, meeting_id: int
    ) -> Sequence[MeetingReminderRecipientDTO]:
        statement = (
            select(User.id, User.telegram_id)
            .join(CommunityMeetingResponse, CommunityMeetingResponse.user_id == User.id)
            .outerjoin(UserNotificationPreference, UserNotificationPreference.user_id == User.id)
            .where(
                CommunityMeetingResponse.meeting_id == meeting_id,
                CommunityMeetingResponse.response == "going",
                User.bot_is_blocked.is_(False),
                or_(
                    UserNotificationPreference.id.is_(None),
                    UserNotificationPreference.meeting_invitations_enabled.is_(True),
                ),
            )
            .order_by(User.id)
        )
        return tuple(
            MeetingReminderRecipientDTO(user_id=user_id, telegram_id=telegram_id)
            for user_id, telegram_id in (await db.execute(statement)).all()
        )

    async def find_delivered_invitee_ids(self, db: AsyncSession, meeting_id: int) -> Sequence[int]:
        statement = select(CommunityMeetingInvitee.user_id).where(
            CommunityMeetingInvitee.meeting_id == meeting_id,
            CommunityMeetingInvitee.invitation_delivered.is_(True),
        )
        return tuple((await db.scalars(statement)).all())

    async def claim_due_reminders(self, db: AsyncSession, now: datetime) -> Sequence[MeetingReminderClaim]:
        claims: list[MeetingReminderClaim] = []
        for hours, attribute, lead in (
            (_REMINDER_24_HOURS, CommunityMeeting.reminder_24h_sent, "24 часа"),
            (1, CommunityMeeting.reminder_1h_sent, "1 час"),
        ):
            due_limit = now + timedelta(hours=hours)
            due_window_start = due_limit - _REMINDER_DELIVERY_GRACE
            due_ids = (
                select(CommunityMeeting.id)
                .where(
                    CommunityMeeting.status == "scheduled",
                    CommunityMeeting.starts_at > now,
                    CommunityMeeting.starts_at <= due_limit,
                    CommunityMeeting.starts_at > due_window_start,
                    attribute.is_(False),
                )
                .with_for_update(skip_locked=True)
            )
            statement = (
                update(CommunityMeeting)
                .where(CommunityMeeting.id.in_(due_ids), attribute.is_(False))
                .values({"reminder_24h_sent" if hours == _REMINDER_24_HOURS else "reminder_1h_sent": True})
                .returning(CommunityMeeting)
            )
            for meeting in (await db.scalars(statement)).all():
                if hours == _REMINDER_24_HOURS:
                    day_before = meeting.starts_at - timedelta(hours=_REMINDER_24_HOURS)
                    was_created_late = meeting.created_at > day_before
                    was_rescheduled_late = meeting.rescheduled_at is not None and meeting.rescheduled_at > day_before
                    if was_created_late or was_rescheduled_late:
                        continue
                claims.append(MeetingReminderClaim(self._to_dto(meeting), meeting.created_at, lead))
        return tuple(claims)

    async def finish_elapsed_meetings(self, db: AsyncSession, now: datetime) -> int:
        statement = (
            update(CommunityMeeting)
            .where(
                CommunityMeeting.status == "scheduled",
                CommunityMeeting.starts_at + timedelta(hours=3) <= now,
            )
            .values(status="finished")
            .returning(CommunityMeeting.id)
        )
        return len((await db.scalars(statement)).all())

    def _audience_select(
        self, audience: str, *, user_id: int | None = None
    ) -> Select[tuple[int, int, bool, bool]] | None:
        conditions = []
        if user_id is not None:
            conditions.append(User.id == user_id)
        if audience == "all":
            audience_condition = True
        elif audience.startswith("competence:"):
            competence_id = int(audience.split(":", maxsplit=1)[1])
            audience_condition = User.id.in_(
                select(UserCompetence.user_id).where(UserCompetence.competence_id == competence_id)
            )
        elif audience.startswith("stack:"):
            audience_condition = User.id.in_(select(UserTechnologyStack.user_id))
        else:
            return None
        return (
            select(
                User.id.label("user_id"),
                User.telegram_id,
                User.bot_is_blocked,
                func.coalesce(UserNotificationPreference.meeting_invitations_enabled, True).label(
                    "invitations_enabled"
                ),
            )
            .outerjoin(UserNotificationPreference, UserNotificationPreference.user_id == User.id)
            .where(*conditions, audience_condition)
        )

    async def _find_stack_identities(self, db: AsyncSession, user_ids: tuple[int, ...]) -> dict[int, frozenset[str]]:
        if not user_ids:
            return {}
        statement = select(UserTechnologyStack.user_id, UserTechnologyStack.technology).where(
            UserTechnologyStack.user_id.in_(user_ids)
        )
        identities: dict[int, set[str]] = {}
        for user_id, technology in (await db.execute(statement)).all():
            try:
                identity = technology_identity(technology)
            except TechnologyStackValidationError:
                identity = technology.strip().casefold()
            identities.setdefault(user_id, set()).add(identity)
        return {user_id: frozenset(user_identities) for user_id, user_identities in identities.items()}

    @staticmethod
    def _to_dto(meeting: CommunityMeeting) -> CommunityMeetingReadDTO:
        starts_at = meeting.starts_at
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)
        return CommunityMeetingReadDTO(
            id=meeting.id,
            title=meeting.title,
            starts_at=starts_at,
            venue=meeting.venue,
            description=meeting.description,
            audience=meeting.audience,
            creator_user_id=meeting.creator_user_id,
            status=meeting.status,
        )
