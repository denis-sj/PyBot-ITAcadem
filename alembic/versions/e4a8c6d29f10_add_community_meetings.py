"""Add meetings, audience snapshots and participant responses.

Revision ID: e4a8c6d29f10
Revises: d8f721b9c3a4
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a8c6d29f10"
down_revision: str | Sequence[str] | None = "d8f721b9c3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the new meeting tables and their indexes."""
    op.create_table(
        "community_meetings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("audience", sa.String(length=200), nullable=False),
        sa.Column("creator_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="scheduled", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rescheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_24h_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reminder_1h_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled', 'finished')", name="ck_community_meetings_status"),
        sa.CheckConstraint(
            "audience = 'all' OR audience LIKE 'competence:%' OR audience LIKE 'stack:%'",
            name="ck_community_meetings_audience",
        ),
        sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_community_meetings_status_starts_at", "community_meetings", ["status", "starts_at"])
    op.create_table(
        "community_meeting_invitees",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("invitation_delivered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["community_meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "user_id", name="uq_community_meeting_invitees_meeting_user"),
    )
    op.create_table(
        "community_meeting_responses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("response", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("response IN ('going', 'not_going')", name="ck_community_meeting_responses_response"),
        sa.ForeignKeyConstraint(["meeting_id"], ["community_meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "user_id", name="uq_community_meeting_responses_meeting_user"),
    )
    op.create_index(
        "ix_community_meeting_responses_meeting_answer", "community_meeting_responses", ["meeting_id", "response"]
    )


def downgrade() -> None:
    """Drop only the meeting tables introduced by this revision."""
    op.drop_index("ix_community_meeting_responses_meeting_answer", table_name="community_meeting_responses")
    op.drop_table("community_meeting_responses")
    op.drop_table("community_meeting_invitees")
    op.drop_index("ix_community_meetings_status_starts_at", table_name="community_meetings")
    op.drop_table("community_meetings")
