"""PostgreSQL upgrade and downgrade coverage for goals, check-ins and preferences."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.database import validate_test_database_url

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BASE_REVISION = "c391d8f472a0"
_MIGRATION_USER_ID = 8_882_001


@pytest.mark.integration
def test_community_checkin_migration_upgrade_and_downgrade_on_postgresql(
    test_database_url: str,
    migrated_test_database: None,
) -> None:
    """Add and remove only community workflow tables while retaining existing user rows."""
    database_url = validate_test_database_url(test_database_url)
    alembic_config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    async def _insert_existing_user() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, first_name, last_name, telegram_id, join_date, academic_points, reputation_points) "
                        "VALUES (:user_id, 'Community', 'Migration', :telegram_id, '2026-01-01', 0, 0) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"user_id": _MIGRATION_USER_ID, "telegram_id": _MIGRATION_USER_ID},
                )
        finally:
            await engine.dispose()

    async def _assert_schema(*, migrated: bool) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                user_name = await connection.scalar(
                    text("SELECT first_name FROM users WHERE id = :user_id"),
                    {"user_id": _MIGRATION_USER_ID},
                )
                assert user_name == "Community"

                for table_name in ("user_goals", "weekly_checkins", "user_notification_preferences"):
                    table = await connection.scalar(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": f"public.{table_name}"},
                    )
                    assert (table is not None) is migrated

                if migrated:
                    await connection.execute(
                        text(
                            "INSERT INTO user_notification_preferences (user_id) VALUES (:user_id) "
                            "ON CONFLICT (user_id) DO NOTHING"
                        ),
                        {"user_id": _MIGRATION_USER_ID},
                    )
                    preferences = (
                        await connection.execute(
                            text(
                                "SELECT checkins_enabled, monthly_summary_enabled, "
                                "achievement_notifications_enabled, streak_warnings_enabled, "
                                "meeting_invitations_enabled FROM user_notification_preferences "
                                "WHERE user_id = :user_id"
                            ),
                            {"user_id": _MIGRATION_USER_ID},
                        )
                    ).one()
                    assert preferences == (True, True, True, True, True)
        finally:
            await engine.dispose()

    async def _remove_user() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": _MIGRATION_USER_ID})
        finally:
            await engine.dispose()

    try:
        asyncio.run(_insert_existing_user())
        command.downgrade(alembic_config, _BASE_REVISION)
        asyncio.run(_assert_schema(migrated=False))
        command.upgrade(alembic_config, "head")
        asyncio.run(_assert_schema(migrated=True))
        command.downgrade(alembic_config, _BASE_REVISION)
        asyncio.run(_assert_schema(migrated=False))
    finally:
        command.upgrade(alembic_config, "head")
        asyncio.run(_remove_user())
