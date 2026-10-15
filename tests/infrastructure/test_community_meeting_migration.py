"""PostgreSQL upgrade and downgrade coverage for community meetings."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.database import validate_test_database_url

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BASE_REVISION = "d8f721b9c3a4"
_MIGRATION_USER_ID = 8_884_101
_MEETING_TABLES = ("community_meetings", "community_meeting_invitees", "community_meeting_responses")


@pytest.mark.integration
def test_community_meeting_migration_upgrade_and_downgrade_on_postgresql(
    test_database_url: str,
    migrated_test_database: None,
) -> None:
    """Create and remove only meeting tables while preserving existing users."""
    database_url = validate_test_database_url(test_database_url)
    alembic_config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    async def insert_existing_user() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, first_name, telegram_id, join_date, academic_points, reputation_points) "
                        "VALUES (:user_id, 'Migration', :telegram_id, '2026-01-01', 0, 0) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"user_id": _MIGRATION_USER_ID, "telegram_id": _MIGRATION_USER_ID},
                )
        finally:
            await engine.dispose()

    async def assert_schema(*, migrated: bool) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                existing_name = await connection.scalar(
                    text("SELECT first_name FROM users WHERE id = :user_id"),
                    {"user_id": _MIGRATION_USER_ID},
                )
                assert existing_name == "Migration"
                for table_name in _MEETING_TABLES:
                    table = await connection.scalar(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": f"public.{table_name}"},
                    )
                    assert (table is not None) is migrated

                if migrated:
                    indexes = await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                            "AND tablename IN ('community_meetings', 'community_meeting_responses')"
                        )
                    )
                    assert {
                        "ix_community_meetings_status_starts_at",
                        "ix_community_meeting_responses_meeting_answer",
                    } <= {row[0] for row in indexes}
                    constraints = await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint WHERE conrelid IN "
                            "('community_meeting_invitees'::regclass, 'community_meeting_responses'::regclass)"
                        )
                    )
                    assert {
                        "uq_community_meeting_invitees_meeting_user",
                        "uq_community_meeting_responses_meeting_user",
                        "ck_community_meeting_responses_response",
                    } <= {row[0] for row in constraints}
                    columns = await connection.execute(
                        text(
                            "SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_name = 'community_meetings'"
                        )
                    )
                    column_types = {name: data_type for name, data_type in columns.all()}
                    assert column_types["starts_at"] == "timestamp with time zone"
                    assert column_types["rescheduled_at"] == "timestamp with time zone"
        finally:
            await engine.dispose()

    async def remove_existing_user() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": _MIGRATION_USER_ID})
        finally:
            await engine.dispose()

    try:
        asyncio.run(insert_existing_user())
        command.downgrade(alembic_config, _BASE_REVISION)
        asyncio.run(assert_schema(migrated=False))
        command.upgrade(alembic_config, "head")
        asyncio.run(assert_schema(migrated=True))
        command.downgrade(alembic_config, _BASE_REVISION)
        asyncio.run(assert_schema(migrated=False))
    finally:
        command.upgrade(alembic_config, "head")
        asyncio.run(remove_existing_user())
