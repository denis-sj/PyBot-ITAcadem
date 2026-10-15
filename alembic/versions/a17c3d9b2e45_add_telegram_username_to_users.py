"""Add Telegram username to user profiles.

Revision ID: a17c3d9b2e45
Revises: c5e9a4f21d30
Create Date: 2026-10-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a17c3d9b2e45"
down_revision: str | Sequence[str] | None = "c5e9a4f21d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable username so existing rows remain valid during rollout."""
    op.add_column("users", sa.Column("telegram_username", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove the stored Telegram username."""
    op.drop_column("users", "telegram_username")
