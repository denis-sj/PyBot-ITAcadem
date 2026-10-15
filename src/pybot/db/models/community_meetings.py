"""ORM models for community meetings, immutable invite snapshots and responses."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base_class import Base


class CommunityMeeting(Base):
    """One scheduled community meeting and its reminder delivery state."""

    __tablename__ = "community_meetings"
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'cancelled', 'finished')", name="ck_community_meetings_status"),
        CheckConstraint(
            "audience = 'all' OR audience LIKE 'competence:%' OR audience LIKE 'stack:%'",
            name="ck_community_meetings_audience",
        ),
        Index("ix_community_meetings_status_starts_at", "status", "starts_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(200), nullable=False)
    creator_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="scheduled")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rescheduled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class CommunityMeetingInvitee(Base):
    """Immutable audience snapshot from creation, including invite delivery state."""

    __tablename__ = "community_meeting_invitees"
    __table_args__ = (UniqueConstraint("meeting_id", "user_id", name="uq_community_meeting_invitees_meeting_user"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("community_meetings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    invitation_delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class CommunityMeetingResponse(Base):
    """The latest RSVP answer from one participant for one meeting."""

    __tablename__ = "community_meeting_responses"
    __table_args__ = (
        UniqueConstraint("meeting_id", "user_id", name="uq_community_meeting_responses_meeting_user"),
        CheckConstraint("response IN ('going', 'not_going')", name="ck_community_meeting_responses_response"),
        Index("ix_community_meeting_responses_meeting_answer", "meeting_id", "response"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("community_meetings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    response: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
