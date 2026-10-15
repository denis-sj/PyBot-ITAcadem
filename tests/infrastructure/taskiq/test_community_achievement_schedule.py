from types import SimpleNamespace
from typing import Any, cast

import pytest

from pybot.core.config import AppSettings
from pybot.infrastructure.taskiq.taskiq_community_achievement_schedule import (
    ACHIEVEMENT_DIGEST_CRON,
    ACHIEVEMENT_DIGEST_SCHEDULE_ID,
    ACHIEVEMENT_TIMEZONE,
    TOP_WEEK_CRON,
    TOP_WEEK_SCHEDULE_ID,
    ensure_community_achievement_schedules,
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
        self.calls: list[tuple[object, str]] = []

    def with_schedule_id(self, schedule_id: str) -> FakeKicker:
        self.schedule_id = schedule_id
        return self

    def with_labels(self, **labels: Any) -> FakeKicker:
        self.labels = labels
        return self

    async def schedule_by_cron(self, source: FakeScheduleSource, cron: str) -> SimpleNamespace:
        self.calls.append((source, cron))
        schedule = SimpleNamespace(
            schedule_id=self.schedule_id,
            cron=cron,
            cron_offset=self.labels["cron_offset"],
        )
        source.schedules.append(schedule)
        return schedule


@pytest.mark.asyncio
async def test_enabled_publications_register_expected_yekaterinburg_schedules(settings_obj: AppSettings) -> None:
    settings_obj.achievements_chat_id = -1001234567890
    settings_obj.achievements_topic_id = 32
    source = FakeScheduleSource()
    kickers: dict[str, FakeKicker] = {}

    def resolve_kicker(task_name: str) -> FakeKicker:
        kicker = FakeKicker()
        kickers[task_name] = kicker
        return kicker

    await ensure_community_achievement_schedules(
        source=cast(Any, source),
        settings=settings_obj,
        resolve_kicker=cast(Any, resolve_kicker),
    )

    schedules = {schedule.schedule_id: schedule for schedule in source.schedules}
    assert set(schedules) == {ACHIEVEMENT_DIGEST_SCHEDULE_ID, TOP_WEEK_SCHEDULE_ID}
    assert schedules[ACHIEVEMENT_DIGEST_SCHEDULE_ID].cron == ACHIEVEMENT_DIGEST_CRON == "0 19 * * *"
    assert schedules[TOP_WEEK_SCHEDULE_ID].cron == TOP_WEEK_CRON == "0 12 * * 1"
    assert all(str(schedule.cron_offset) == ACHIEVEMENT_TIMEZONE for schedule in schedules.values())
    assert set(kickers) == {
        "community_achievements.publish_daily_digest",
        "community_achievements.publish_top_week",
    }


@pytest.mark.asyncio
async def test_disabled_publications_remove_existing_schedules(settings_obj: AppSettings) -> None:
    source = FakeScheduleSource(
        [
            SimpleNamespace(schedule_id=ACHIEVEMENT_DIGEST_SCHEDULE_ID),
            SimpleNamespace(schedule_id=TOP_WEEK_SCHEDULE_ID),
        ]
    )

    await ensure_community_achievement_schedules(
        source=cast(Any, source),
        settings=settings_obj,
        resolve_kicker=cast(Any, lambda _task_name: FakeKicker()),
    )

    assert source.deleted_ids == [ACHIEVEMENT_DIGEST_SCHEDULE_ID, TOP_WEEK_SCHEDULE_ID]
    assert source.schedules == []
