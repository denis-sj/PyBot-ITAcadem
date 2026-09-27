"""Support manual attendance and change history.

Revision ID: c5e9a4f21d30
Revises: 8d6ac9f0b6a0
Create Date: 2026-09-27 12:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e9a4f21d30"
down_revision: str | Sequence[str] | None = "8d6ac9f0b6a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: make import_id and attendance_date_id nullable; add attendance_change_history table."""
    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.alter_column("import_id", existing_type=sa.BigInteger(), nullable=True)
        batch_op.alter_column("attendance_date_id", existing_type=sa.BigInteger(), nullable=True)

    op.create_table(
        "attendance_change_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("old_value", sa.String(length=100), nullable=True),
        sa.Column("new_value", sa.String(length=100), nullable=True),
        sa.Column("admin_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attendance_change_history_user_id", "attendance_change_history", ["user_id"], unique=False)
    op.create_index(
        "ix_attendance_change_history_visit_date", "attendance_change_history", ["visit_date"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema: drop attendance_change_history table and restore non-null columns."""
    op.drop_index("ix_attendance_change_history_visit_date", table_name="attendance_change_history")
    op.drop_index("ix_attendance_change_history_user_id", table_name="attendance_change_history")
    op.drop_table("attendance_change_history")

    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.alter_column("attendance_date_id", existing_type=sa.BigInteger(), nullable=False)
        batch_op.alter_column("import_id", existing_type=sa.BigInteger(), nullable=False)
