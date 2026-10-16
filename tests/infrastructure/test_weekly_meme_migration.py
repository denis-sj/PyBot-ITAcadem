"""PostgreSQL upgrade and downgrade coverage for weekly meme publication state."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.database import validate_test_database_url

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS_REVISION = "e4a8c6d29f10"


@pytest.mark.integration
def test_weekly_meme_migration_upgrade_and_downgrade_on_postgresql(
    test_database_url: str,
    migrated_test_database: None,
) -> None:
    """Add and remove only the meme publication table and its indexes."""
    database_url = validate_test_database_url(test_database_url)
    alembic_config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    async def assert_table_exists(expected: bool) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                table = await connection.scalar(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": "public.weekly_meme_publications"},
                )
                assert (table is not None) is expected
                if expected:
                    indexes = await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                            "AND tablename = 'weekly_meme_publications'"
                        )
                    )
                    assert {
                        "uq_weekly_meme_publications_week_published",
                        "ix_weekly_meme_publications_week_status",
                    } <= {row[0] for row in indexes}
                    constraints = await connection.execute(
                        text("SELECT conname FROM pg_constraint WHERE conrelid = 'weekly_meme_publications'::regclass")
                    )
                    assert {
                        "ck_weekly_meme_publications_status",
                        "ck_weekly_meme_publications_delivery_state",
                    } <= {row[0] for row in constraints}
        finally:
            await engine.dispose()

    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
        asyncio.run(assert_table_exists(False))
        command.upgrade(alembic_config, "head")
        asyncio.run(assert_table_exists(True))
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
        asyncio.run(assert_table_exists(False))
    finally:
        command.upgrade(alembic_config, "head")
