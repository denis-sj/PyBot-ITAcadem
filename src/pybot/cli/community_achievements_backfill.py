"""Silently award community achievements discovered in existing history."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dishka import AsyncContainer


async def _backfill() -> int:
    """Run the idempotent backfill through the application's DI container."""
    containers_module = import_module("pybot.di.containers")
    services_module = import_module("pybot.services")
    container: AsyncContainer = await containers_module.setup_container()
    try:
        async with container() as request_container:
            service = await request_container.get(services_module.CommunityAchievementService)
            return await service.backfill_history_silently()
    finally:
        await container.close()


def main() -> None:
    """Run the historical backfill without sending DMs or publications."""
    inserted_count = asyncio.run(_backfill())
    print(f"Молча начислено исторических достижений: {inserted_count}")


if __name__ == "__main__":
    main()
