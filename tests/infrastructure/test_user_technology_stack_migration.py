"""PostgreSQL upgrade and downgrade coverage for the participant stack table."""

import asyncio
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.database import validate_test_database_url

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_USER_ID = 8_882_020
_AWARD_ID = 8_882_021


@pytest.mark.integration
def test_user_technology_stack_migration_upgrade_and_downgrade_on_postgresql(
    test_database_url: str,
    migrated_test_database: None,
) -> None:
    """The additive migration preserves existing users and awards across rollback."""
    database_url = validate_test_database_url(test_database_url)
    alembic_config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    async def _insert_existing_rows() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, first_name, last_name, telegram_id, join_date, academic_points, "
                        "reputation_points, telegram_username, bot_is_blocked) "
                        "VALUES (:user_id, 'Stack', 'Migration', :telegram_id, '2026-01-01', 0, 0, 'stack_test', false) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"user_id": _USER_ID, "telegram_id": _USER_ID},
                )
                await connection.execute(
                    text(
                        "INSERT INTO user_achievement_awards "
                        "(id, user_id, code, achieved_at, is_published, is_notified) "
                        "VALUES (:award_id, :user_id, 'first_step', '2026-01-01T00:00:00+00:00', true, true) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"award_id": _AWARD_ID, "user_id": _USER_ID},
                )
        finally:
            await engine.dispose()

    async def _assert_existing_rows_and_stack_table(*, stack_table_exists: bool) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                user_name = await connection.scalar(
                    text("SELECT first_name FROM users WHERE id = :user_id"),
                    {"user_id": _USER_ID},
                )
                award_code = await connection.scalar(
                    text("SELECT code FROM user_achievement_awards WHERE id = :award_id"),
                    {"award_id": _AWARD_ID},
                )
                stack_table = await connection.scalar(text("SELECT to_regclass('public.user_technology_stack')"))
                assert user_name == "Stack"
                assert award_code == "first_step"
                assert (stack_table is not None) is stack_table_exists

                if stack_table_exists:
                    await connection.execute(
                        text(
                            "INSERT INTO user_technology_stack (user_id, technology, level) "
                            "VALUES (:user_id, 'Docker', 'learning')"
                        ),
                        {"user_id": _USER_ID},
                    )
                    entry = (
                        await connection.execute(
                            text(
                                "SELECT technology, level, updated_at FROM user_technology_stack "
                                "WHERE user_id = :user_id"
                            ),
                            {"user_id": _USER_ID},
                        )
                    ).one()
                    assert entry.technology == "Docker"
                    assert entry.level == "learning"
                    assert entry.updated_at is not None
        finally:
            await engine.dispose()

    async def _remove_existing_rows() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM users WHERE id = :user_id"),
                    {"user_id": _USER_ID},
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_insert_existing_rows())
        command.downgrade(alembic_config, "b17c4d8e6a21")
        asyncio.run(_assert_existing_rows_and_stack_table(stack_table_exists=False))

        command.upgrade(alembic_config, "c391d8f472a0")
        asyncio.run(_assert_existing_rows_and_stack_table(stack_table_exists=True))

        command.downgrade(alembic_config, "b17c4d8e6a21")
        asyncio.run(_assert_existing_rows_and_stack_table(stack_table_exists=False))
    finally:
        command.upgrade(alembic_config, "head")
        asyncio.run(_remove_existing_rows())
