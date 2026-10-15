"""TaskIQ schedule contract for meeting reminders and automatic finishing."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from pybot.infrastructure.taskiq.taskiq_community_meeting_schedule import (
    MEETING_MAINTENANCE_CRON,
    MEETING_MAINTENANCE_SCHEDULE_ID,
    MEETING_MAINTENANCE_TASK_NAME,
    MEETING_TIMEZONE,
    ensure_community_meeting_schedule,
)


class FakeScheduleSource:
    def __init__(self, schedules: list[SimpleNamespace] | None = None) -> None:
        self.schedules = schedules or []
        self.deleted_ids: list[str] = []

    async def get_schedules(self) -> list[SimpleNamespace]:
        return self.schedules

    async def delete_schedule(self, schedule_id: str) -> None:
        self.deleted_ids.append(schedule_id)
        self.schedules = [item for item in self.schedules if item.schedule_id != schedule_id]


class FakeKicker:
    def __init__(self) -> None:
        self.schedule_id: str | None = None
        self.labels: dict[str, Any] = {}

    def with_schedule_id(self, schedule_id: str) -> FakeKicker:
        self.schedule_id = schedule_id
        return self

    def with_labels(self, **labels: Any) -> FakeKicker:
        self.labels = labels
        return self

    async def schedule_by_cron(self, source: FakeScheduleSource, cron: str) -> SimpleNamespace:
        schedule = SimpleNamespace(
            schedule_id=self.schedule_id,
            task_name=MEETING_MAINTENANCE_TASK_NAME,
            cron=cron,
            cron_offset=self.labels["cron_offset"],
        )
        source.schedules.append(schedule)
        return schedule


@pytest.mark.asyncio
async def test_meeting_maintenance_schedule_runs_every_minute_in_yekaterinburg() -> None:
    source = FakeScheduleSource()
    kicker = FakeKicker()

    await ensure_community_meeting_schedule(source=cast(Any, source), resolve_kicker=cast(Any, lambda: kicker))

    assert len(source.schedules) == 1
    schedule = source.schedules[0]
    assert schedule.schedule_id == MEETING_MAINTENANCE_SCHEDULE_ID
    assert schedule.task_name == MEETING_MAINTENANCE_TASK_NAME
    assert schedule.cron == MEETING_MAINTENANCE_CRON == "* * * * *"
    assert str(schedule.cron_offset) == MEETING_TIMEZONE == "Asia/Yekaterinburg"


@pytest.mark.asyncio
async def test_meeting_schedule_registration_is_idempotent() -> None:
    source = FakeScheduleSource(
        [
            SimpleNamespace(
                schedule_id=MEETING_MAINTENANCE_SCHEDULE_ID,
                task_name=MEETING_MAINTENANCE_TASK_NAME,
                cron=MEETING_MAINTENANCE_CRON,
                cron_offset=MEETING_TIMEZONE,
            )
        ]
    )
    kickers: list[FakeKicker] = []

    def resolve_kicker() -> FakeKicker:
        kicker = FakeKicker()
        kickers.append(kicker)
        return kicker

    await ensure_community_meeting_schedule(source=cast(Any, source), resolve_kicker=cast(Any, resolve_kicker))

    assert len(source.schedules) == 1
    assert kickers == []
    assert source.deleted_ids == []
