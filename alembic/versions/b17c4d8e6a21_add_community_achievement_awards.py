"""Add community achievement awards and bot availability state.

Revision ID: b17c4d8e6a21
Revises: c5e9a4f21d30
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b17c4d8e6a21"
down_revision: str | Sequence[str] | None = "a17c3d9b2e45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive award table and blocked-bot marker."""
    op.add_column(
        "users",
        sa.Column("bot_is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "user_achievement_awards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("achieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_notified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "code", name="uq_user_achievement_awards_user_code"),
    )
    op.create_index("ix_user_achievement_awards_user_id", "user_achievement_awards", ["user_id"], unique=False)


def downgrade() -> None:
    """Remove only the community achievement additions."""
    op.drop_index("ix_user_achievement_awards_user_id", table_name="user_achievement_awards")
    op.drop_table("user_achievement_awards")
    op.drop_column("users", "bot_is_blocked")
