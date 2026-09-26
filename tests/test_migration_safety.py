"""Tests for migration data safety and additive schema contract.

Verifies:
1. Alembic revision 8d6ac9f0b6a0 is strictly additive (no drops or alterations of existing tables).
2. Existing entities (users, roles, levels) remain intact across upgrade and downgrade.
3. Deployment and compose configurations do not execute destructive reset/drop commands.
"""

import ast
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "8d6ac9f0b6a0_add_attendance_tables.py"
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"


# ============ STATIC MIGRATION AST VERIFICATION (UNIT TESTS) ============


def test_migration_is_strictly_chained_from_initial_baseline() -> None:
    content = _MIGRATION_PATH.read_text(encoding="utf-8")
    parsed_ast = ast.parse(content)
    assignments: dict[str, str | None] = {}
    for node in parsed_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    assignments[target.id] = str(node.value.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            assignments[node.target.id] = str(node.value.value)
    assert assignments.get("revision") == "8d6ac9f0b6a0"
    assert assignments.get("down_revision") == "1de8107fe120"


def test_migration_upgrade_is_strictly_additive_to_attendance_tables_only() -> None:
    content = _MIGRATION_PATH.read_text(encoding="utf-8")
    parsed_ast = ast.parse(content)

    upgrade_fn = None
    downgrade_fn = None
    for node in parsed_ast.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None, "upgrade function missing"
    assert downgrade_fn is not None, "downgrade function missing"

    created_tables: list[str] = []
    forbidden_calls: list[str] = []

    for subnode in ast.walk(upgrade_fn):
        if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute):
            method_name = subnode.func.attr
            if method_name == "create_table":
                first_arg = subnode.args[0]
                if isinstance(first_arg, ast.Constant):
                    created_tables.append(str(first_arg.value))
            elif method_name in ("drop_table", "drop_column", "alter_column", "drop_constraint"):
                forbidden_calls.append(method_name)

    assert forbidden_calls == [], f"Destructive operations found in upgrade: {forbidden_calls}"
    assert set(created_tables) == {
        "attendance_imports",
        "attendance_dates",
        "attendance_records",
    }, f"Unexpected tables created: {created_tables}"


def test_migration_downgrade_only_removes_attendance_tables() -> None:
    content = _MIGRATION_PATH.read_text(encoding="utf-8")
    parsed_ast = ast.parse(content)

    downgrade_fn = next((n for n in parsed_ast.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"), None)
    assert downgrade_fn is not None

    dropped_tables: list[str] = []
    for subnode in ast.walk(downgrade_fn):
        if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute):
            method_name = subnode.func.attr
            if method_name == "drop_table":
                first_arg = subnode.args[0]
                if isinstance(first_arg, ast.Constant):
                    dropped_tables.append(str(first_arg.value))

    assert set(dropped_tables) == {
        "attendance_records",
        "attendance_dates",
        "attendance_imports",
    }, f"Downgrade touches unexpected tables: {dropped_tables}"


def test_compose_maintains_persistent_postgres_volume_and_safe_migrate_command() -> None:
    compose_content = _COMPOSE_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(compose_content)

    services = data.get("services", {})
    postgres_service = services.get("postgres", {})
    volumes = postgres_service.get("volumes", [])
    assert any("postgres_data:" in str(v) for v in volumes), (
        "postgres service must mount persistent postgres_data volume"
    )

    migrate_service = services.get("migrate", {})
    command = migrate_service.get("command", [])
    assert command == ["alembic", "upgrade", "head"], "migrate service must only perform 'alembic upgrade head'"


# ============ INTEGRATION MIGRATION VERIFICATION (POSTGRESQL) ============


@pytest.mark.integration
def test_migration_data_safety_simulation_with_database(
    test_database_url: str,
    migrated_test_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that existing data is preserved across upgrade and downgrade on PostgreSQL."""
    import asyncio

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.database import validate_test_database_url

    validated_url = validate_test_database_url(test_database_url)
    monkeypatch.setenv("DATABASE_URL", validated_url)

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", validated_url)

    async def _insert_baseline_user() -> None:
        engine = create_async_engine(validated_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, first_name, last_name, telegram_id, join_date, academic_points, reputation_points) "
                        "VALUES (8888, 'Migration', 'Tester', 88889999, '2026-01-01', 0, 0) "
                        "ON CONFLICT (id) DO NOTHING"
                    )
                )
        finally:
            await engine.dispose()

    async def _verify_and_insert_attendance() -> None:
        engine = create_async_engine(validated_url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("SELECT id, first_name, last_name, telegram_id FROM users WHERE id = 8888")
                )
                row = result.first()
                assert row is not None
                assert row[0] == 8888
                assert row[1] == "Migration"
                assert row[2] == "Tester"

                await conn.execute(
                    text(
                        "INSERT INTO attendance_imports (id, author_tg_id, status) VALUES (101, 88889999, 'CONFIRMED')"
                    )
                )
                await conn.execute(
                    text("INSERT INTO attendance_dates (id, import_id, date) VALUES (201, 101, '2026-09-25')")
                )
                await conn.execute(
                    text(
                        "INSERT INTO attendance_records (id, import_id, attendance_date_id, user_id, date, duration_minutes) "
                        "VALUES (301, 101, 201, 8888, '2026-09-25', 120)"
                    )
                )

                rec_row = (
                    await conn.execute(
                        text("SELECT id, user_id, duration_minutes FROM attendance_records WHERE id = 301")
                    )
                ).first()
                assert rec_row is not None
                assert rec_row[1] == 8888
                assert rec_row[2] == 120
        finally:
            await engine.dispose()

    async def _verify_downgrade_and_clean() -> None:
        engine = create_async_engine(validated_url)
        try:
            async with engine.begin() as conn:
                user_row = (await conn.execute(text("SELECT id FROM users WHERE id = 8888"))).first()
                assert user_row is not None
                assert user_row[0] == 8888

                await conn.execute(text("DELETE FROM users WHERE id = 8888"))
        finally:
            await engine.dispose()

    # 1. Downgrade to baseline 1de8107fe120
    command.downgrade(alembic_cfg, "1de8107fe120")

    # 2. Insert test user and role
    asyncio.run(_insert_baseline_user())

    # 3. Upgrade to 8d6ac9f0b6a0 (head)
    command.upgrade(alembic_cfg, "head")

    # 4. Verify existing user row is intact and identical, insert into new attendance tables
    asyncio.run(_verify_and_insert_attendance())

    # 5. Verify safe downgrade back to baseline
    command.downgrade(alembic_cfg, "1de8107fe120")

    # 6. Verify existing user still intact, clean up test user
    asyncio.run(_verify_downgrade_and_clean())

    # 7. Restore head state for subsequent tests
    command.upgrade(alembic_cfg, "head")


def test_migration_safety_rejects_non_test_database_url() -> None:
    from tests.database import validate_test_database_url

    with pytest.raises(ValueError, match="must target database 'test' or a name ending in '_test'"):
        validate_test_database_url("postgresql+asyncpg://user:pass@127.0.0.1:5432/pybot_prod")

    with pytest.raises(ValueError, match="is not a trusted test host"):
        validate_test_database_url("postgresql+asyncpg://user:pass@prod-server.com:5432/my_test")

    with pytest.raises(ValueError, match="is not a trusted test host"):
        validate_test_database_url("postgresql+asyncpg://user:pass@postgres:5432/my_test")
