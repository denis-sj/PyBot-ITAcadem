from types import SimpleNamespace
from typing import Any, cast

import pytest

from pybot.core.config import AppSettings
from pybot.infrastructure.taskiq.taskiq_meme_schedule import (
    MEME_CRON,
    MEME_SCHEDULE_ID,
    MEME_TASK_NAME,
    MEME_TIMEZONE,
    ensure_weekly_meme_schedule,
)


class FakeScheduleSource:
    def __init__(self, schedules: list[SimpleNamespace] | None = None) -> None:
        self.schedules = schedules or []
        self.deleted_ids: list[str] = []

    async def get_schedules(self) -> list[SimpleNamespace]:
        return self.schedules

    async def delete_schedule(self, schedule_id: str) -> None:
        self.deleted_ids.append(schedule_id)
        self.schedules = [schedule for schedule in self.schedules if schedule.schedule_id != schedule_id]


class FakeKicker:
    def __init__(self, source: FakeScheduleSource) -> None:
        self.source = source
        self.schedule_id: str | None = None
        self.labels: dict[str, Any] = {}
        self.crons: list[str] = []

    def with_schedule_id(self, schedule_id: str) -> FakeKicker:
        self.schedule_id = schedule_id
        return self

    def with_labels(self, **labels: Any) -> FakeKicker:
        self.labels = labels
        return self

    async def schedule_by_cron(self, source: FakeScheduleSource, cron: str) -> None:
        self.crons.append(cron)
        source.schedules.append(
            SimpleNamespace(
                schedule_id=self.schedule_id,
                task_name=MEME_TASK_NAME,
                cron=cron,
                cron_offset=self.labels["cron_offset"],
            )
        )


@pytest.mark.asyncio
async def test_weekly_meme_schedule_is_wednesday_at_1616_in_yekaterinburg() -> None:
    source = FakeScheduleSource()
    kicker = FakeKicker(source)

    await ensure_weekly_meme_schedule(
        source=cast(Any, source),
        settings=cast(AppSettings, SimpleNamespace(meme_publication_enabled=True)),
        resolve_kicker=lambda: cast(Any, kicker),
    )

    assert len(source.schedules) == 1
    schedule = source.schedules[0]
    assert schedule.schedule_id == MEME_SCHEDULE_ID
    assert schedule.task_name == MEME_TASK_NAME
    assert schedule.cron == MEME_CRON == "16 16 * * 3"
    assert str(schedule.cron_offset) == MEME_TIMEZONE == "Asia/Yekaterinburg"


@pytest.mark.asyncio
async def test_weekly_meme_schedule_registration_is_idempotent() -> None:
    schedule = SimpleNamespace(
        schedule_id=MEME_SCHEDULE_ID,
        task_name=MEME_TASK_NAME,
        cron=MEME_CRON,
        cron_offset=MEME_TIMEZONE,
    )
    source = FakeScheduleSource([schedule])
    kicker = FakeKicker(source)

    await ensure_weekly_meme_schedule(
        source=cast(Any, source),
        settings=cast(AppSettings, SimpleNamespace(meme_publication_enabled=True)),
        resolve_kicker=lambda: cast(Any, kicker),
    )

    assert source.schedules == [schedule]
    assert kicker.crons == []
    assert source.deleted_ids == []


@pytest.mark.asyncio
async def test_schedule_is_removed_when_feature_is_disabled() -> None:
    source = FakeScheduleSource(
        [
            SimpleNamespace(
                schedule_id=MEME_SCHEDULE_ID, task_name=MEME_TASK_NAME, cron=MEME_CRON, cron_offset=MEME_TIMEZONE
            )
        ]
    )
    kicker = FakeKicker(source)

    await ensure_weekly_meme_schedule(
        source=cast(Any, source),
        settings=cast(AppSettings, SimpleNamespace(meme_publication_enabled=False)),
        resolve_kicker=lambda: cast(Any, kicker),
    )

    assert source.schedules == []
    assert source.deleted_ids == [MEME_SCHEDULE_ID]
    assert kicker.crons == []
