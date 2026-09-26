"""Add attendance tables for photo attendance accounting.

Revision ID: 8d6ac9f0b6a0
Revises: 1de8107fe120
Create Date: 2026-09-25 16:35:58.864612

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d6ac9f0b6a0"
down_revision: str | Sequence[str] | None = "1de8107fe120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: create attendance imports, dates, and records tables."""
    op.create_table(
        "attendance_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("author_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("photo_data", postgresql.BYTEA(), nullable=True),
        sa.Column("photo_mime_type", sa.Text(), nullable=True),
        sa.Column("photo_file_size", sa.Integer(), nullable=True),
        sa.Column("photo_checksum", sa.Text(), nullable=True),
        sa.Column("raw_ai_json", sa.Text(), nullable=True),
        sa.Column("draft_state_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_tg_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "attendance_dates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["attendance_imports.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", name="uq_attendance_dates_date"),
    )
    op.create_index("ix_attendance_dates_date", "attendance_dates", ["date"], unique=True)

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=False),
        sa.Column("attendance_date_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_empty_time", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="photo_ai"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["attendance_date_id"],
            ["attendance_dates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["attendance_imports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_attendance_records_user_date"),
    )
    op.create_index("ix_attendance_records_date", "attendance_records", ["date"], unique=False)
    op.create_index("ix_attendance_records_user_id", "attendance_records", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema: drop attendance tables."""
    op.drop_index("ix_attendance_records_user_id", table_name="attendance_records")
    op.drop_index("ix_attendance_records_date", table_name="attendance_records")
    op.drop_table("attendance_records")
    op.drop_index("ix_attendance_dates_date", table_name="attendance_dates")
    op.drop_table("attendance_dates")
    op.drop_table("attendance_imports")
