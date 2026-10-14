"""PostgreSQL upgrade and downgrade coverage for community achievement schema."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.database import validate_test_database_url

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_USER_ID = 8_881_004
_ATTENDANCE_IMPORT_ID = 8_881_005
_ATTENDANCE_DATE_ID = 8_881_006
_ATTENDANCE_RECORD_ID = 8_881_007


@pytest.mark.integration
def test_community_achievement_migration_upgrade_and_downgrade_on_postgresql(
    test_database_url: str,
    migrated_test_database: None,
) -> None:
    """Upgrade and downgrade b17 while preserving users and attendance data."""
    database_url = validate_test_database_url(test_database_url)
    alembic_config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    async def _insert_existing_data() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, first_name, last_name, telegram_id, join_date, academic_points, "
                        "reputation_points, telegram_username) "
                        "VALUES (:user_id, 'Migration', 'Community', :telegram_id, '2026-01-01', 0, 0, 'migration_user') "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"user_id": _USER_ID, "telegram_id": _USER_ID},
                )
                await connection.execute(
                    text(
                        "INSERT INTO attendance_imports (id, author_tg_id, status) "
                        "VALUES (:import_id, :telegram_id, 'CONFIRMED') ON CONFLICT (id) DO NOTHING"
                    ),
                    {"import_id": _ATTENDANCE_IMPORT_ID, "telegram_id": _USER_ID},
                )
                await connection.execute(
                    text(
                        "INSERT INTO attendance_dates (id, import_id, date) "
                        "VALUES (:date_id, :import_id, '2026-09-25') ON CONFLICT (id) DO NOTHING"
                    ),
                    {"date_id": _ATTENDANCE_DATE_ID, "import_id": _ATTENDANCE_IMPORT_ID},
                )
                await connection.execute(
                    text(
                        "INSERT INTO attendance_records "
                        "(id, import_id, attendance_date_id, user_id, date, duration_minutes) "
                        "VALUES (:record_id, :import_id, :date_id, :user_id, '2026-09-25', 120) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "record_id": _ATTENDANCE_RECORD_ID,
                        "import_id": _ATTENDANCE_IMPORT_ID,
                        "date_id": _ATTENDANCE_DATE_ID,
                        "user_id": _USER_ID,
                    },
                )
        finally:
            await engine.dispose()

    async def _assert_existing_data_and_new_schema(*, community_schema_exists: bool) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                user = (
                    await connection.execute(
                        text("SELECT first_name, telegram_username FROM users WHERE id = :user_id"),
                        {"user_id": _USER_ID},
                    )
                ).one()
                attendance = (
                    await connection.execute(
                        text("SELECT duration_minutes FROM attendance_records WHERE id = :record_id"),
                        {"record_id": _ATTENDANCE_RECORD_ID},
                    )
                ).one()
                assert user == ("Migration", "migration_user")
                assert attendance.duration_minutes == 120

                award_table = await connection.scalar(text("SELECT to_regclass('public.user_achievement_awards')"))
                blocked_column_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'bot_is_blocked'"
                    )
                )
                assert (award_table is not None) is community_schema_exists
                assert blocked_column_count == int(community_schema_exists)

                if community_schema_exists:
                    blocked = await connection.scalar(
                        text("SELECT bot_is_blocked FROM users WHERE id = :user_id"),
                        {"user_id": _USER_ID},
                    )
                    assert blocked is False
        finally:
            await engine.dispose()

    async def _remove_existing_data() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": _USER_ID})
                await connection.execute(
                    text("DELETE FROM attendance_imports WHERE id = :import_id"),
                    {"import_id": _ATTENDANCE_IMPORT_ID},
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_insert_existing_data())
        command.downgrade(alembic_config, "a17c3d9b2e45")
        asyncio.run(_assert_existing_data_and_new_schema(community_schema_exists=False))

        command.upgrade(alembic_config, "b17c4d8e6a21")
        asyncio.run(_assert_existing_data_and_new_schema(community_schema_exists=True))

        command.downgrade(alembic_config, "a17c3d9b2e45")
        asyncio.run(_assert_existing_data_and_new_schema(community_schema_exists=False))
    finally:
        command.upgrade(alembic_config, "head")
        asyncio.run(_remove_existing_data())
