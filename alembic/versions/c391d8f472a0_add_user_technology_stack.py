"""Add the participant technology stack.

Revision ID: c391d8f472a0
Revises: b17c4d8e6a21
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c391d8f472a0"
down_revision: str | Sequence[str] | None = "b17c4d8e6a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create an empty table for participant-owned technologies."""
    op.create_table(
        "user_technology_stack",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("technology", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("level IN ('know', 'learning')", name="ck_user_technology_stack_level"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "technology", name="uq_user_technology_stack_user_technology"),
    )
    op.create_index("ix_user_technology_stack_user_id", "user_technology_stack", ["user_id"], unique=False)


def downgrade() -> None:
    """Remove only the participant technology stack table."""
    op.drop_index("ix_user_technology_stack_user_id", table_name="user_technology_stack")
    op.drop_table("user_technology_stack")
