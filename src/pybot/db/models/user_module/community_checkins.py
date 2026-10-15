"""ORM tables for participant goals, weekly check-ins and notification preferences."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...base_class import Base


class UserGoal(Base):
    """One goal history row; a partial index permits one active goal per participant."""

    __tablename__ = "user_goals"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'achieved', 'cancelled')", name="ck_user_goals_status"),
        Index(
            "uq_user_goals_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_user_goals_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    achieved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WeeklyCheckin(Base):
    """A participant's one response opportunity for a Monday-starting campaign week."""

    __tablename__ = "weekly_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_weekly_checkins_user_week"),
        CheckConstraint(
            "status IS NULL OR status IN "
            "('in_progress', 'stuck', 'no_motivation', 'goal_achieved', 'newbie', 'no_substantive_answer')",
            name="ck_weekly_checkins_status",
        ),
        Index("ix_weekly_checkins_week_start", "week_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    week_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    goal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user_goals.id", ondelete="SET NULL"))
    prompted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str | None] = mapped_column(String(32))
    help_topic: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserNotificationPreference(Base):
    """Per-user opt-outs; missing preferences behave as the all-enabled defaults."""

    __tablename__ = "user_notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_notification_preferences_user"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    checkins_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    monthly_summary_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    achievement_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    streak_warnings_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    meeting_invitations_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_streak_warning_week_start: Mapped[dt.date | None] = mapped_column(Date)
