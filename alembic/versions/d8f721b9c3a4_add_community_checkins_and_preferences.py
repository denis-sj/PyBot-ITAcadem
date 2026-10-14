"""Add community goals, weekly check-ins and notification preferences.

Revision ID: d8f721b9c3a4
Revises: c391d8f472a0
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f721b9c3a4"
down_revision: str | Sequence[str] | None = "c391d8f472a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only new tables and indexes for the community check-in flow."""
    op.create_table(
        "user_goals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'achieved', 'cancelled')", name="ck_user_goals_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_user_goals_active_user",
        "user_goals",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_user_goals_user_created_at", "user_goals", ["user_id", "created_at"], unique=False)

    op.create_table(
        "weekly_checkins",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("goal_id", sa.BigInteger(), nullable=True),
        sa.Column("prompted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("help_topic", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IS NULL OR status IN "
            "('in_progress', 'stuck', 'no_motivation', 'goal_achieved', 'newbie', 'no_substantive_answer')",
            name="ck_weekly_checkins_status",
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["user_goals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "week_start", name="uq_weekly_checkins_user_week"),
    )
    op.create_index("ix_weekly_checkins_week_start", "weekly_checkins", ["week_start"], unique=False)

    op.create_table(
        "user_notification_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("checkins_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("monthly_summary_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("achievement_notifications_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("streak_warnings_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("meeting_invitations_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_streak_warning_week_start", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_notification_preferences_user"),
    )


def downgrade() -> None:
    """Drop only the tables introduced by this revision."""
    op.drop_table("user_notification_preferences")
    op.drop_index("ix_weekly_checkins_week_start", table_name="weekly_checkins")
    op.drop_table("weekly_checkins")
    op.drop_index("ix_user_goals_user_created_at", table_name="user_goals")
    op.drop_index("uq_user_goals_active_user", table_name="user_goals")
    op.drop_table("user_goals")
