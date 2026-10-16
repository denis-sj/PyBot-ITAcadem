"""Add idempotent weekly meme publication records.

Revision ID: f2e6a17b9c40
Revises: e4a8c6d29f10
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2e6a17b9c40"
down_revision: str | Sequence[str] | None = "e4a8c6d29f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the additive weekly meme publication table and indexes."""
    op.create_table(
        "weekly_meme_publications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("publication_week", sa.Date(), nullable=False),
        sa.Column("lemmy_post_id", sa.Text(), nullable=False),
        sa.Column("image_urls", sa.JSON(), nullable=False),
        sa.Column("community", sa.String(length=200), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column(
            "telegram_message_ids",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("delivery_state", sa.String(length=16), nullable=False),
        sa.Column("replaced_by_id", sa.BigInteger(), nullable=True),
        sa.Column("replaced_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('published', 'replaced', 'test')",
            name="ck_weekly_meme_publications_status",
        ),
        sa.CheckConstraint(
            "delivery_state IN ('pending', 'complete', 'failed')",
            name="ck_weekly_meme_publications_delivery_state",
        ),
        sa.ForeignKeyConstraint(["replaced_by_id"], ["weekly_meme_publications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_weekly_meme_publications_week_published",
        "weekly_meme_publications",
        ["publication_week"],
        unique=True,
        postgresql_where=sa.text("status = 'published' AND delivery_state = 'complete'"),
    )
    op.create_index(
        "ix_weekly_meme_publications_week_status",
        "weekly_meme_publications",
        ["publication_week", "status"],
    )


def downgrade() -> None:
    """Drop only the weekly meme publication table introduced by this revision."""
    op.drop_index("ix_weekly_meme_publications_week_status", table_name="weekly_meme_publications")
    op.drop_index("uq_weekly_meme_publications_week_published", table_name="weekly_meme_publications")
    op.drop_table("weekly_meme_publications")
