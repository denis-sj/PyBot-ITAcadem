from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

from ....core.config import AppSettings
from . import (
    attendance,
    broadcast,
    community_achievements,
    community_checkins,
    community_meetings,
    leaderboard,
    notification,
    system,
)


@dataclass(slots=True)
class TaskRegistry:
    broadcast_task: AsyncTaskiqDecoratedTask[..., Any]
    notification_task: AsyncTaskiqDecoratedTask[..., Any]
    system_task: AsyncTaskiqDecoratedTask[..., Any]
    weekly_leaderboard_task: AsyncTaskiqDecoratedTask[..., Any]
    attendance_cleanup_task: AsyncTaskiqDecoratedTask[..., int]
    achievement_recalculation_task: AsyncTaskiqDecoratedTask[..., Any]
    achievement_digest_task: AsyncTaskiqDecoratedTask[..., Any]
    achievement_top_week_task: AsyncTaskiqDecoratedTask[..., Any]
    checkin_prompts_task: AsyncTaskiqDecoratedTask[..., Any]
    checkin_reminders_task: AsyncTaskiqDecoratedTask[..., Any]
    streak_warnings_task: AsyncTaskiqDecoratedTask[..., Any]
    pulse_report_task: AsyncTaskiqDecoratedTask[..., Any]
    community_meeting_task: AsyncTaskiqDecoratedTask[..., Any]


def register_all_tasks(*, broker: AsyncBroker, settings: AppSettings) -> TaskRegistry:
    achievement_tasks = community_achievements.register_tasks(broker=broker, settings=settings)
    checkin_tasks = community_checkins.register_tasks(broker=broker, settings=settings)
    meeting_task = community_meetings.register_tasks(broker=broker, settings=settings)
    return TaskRegistry(
        broadcast_task=broadcast.register_tasks(broker=broker),
        notification_task=notification.register_tasks(broker=broker),
        system_task=system.register_tasks(broker=broker),
        weekly_leaderboard_task=leaderboard.register_tasks(broker=broker, settings=settings),
        attendance_cleanup_task=attendance.register_tasks(broker=broker, settings=settings),
        achievement_recalculation_task=achievement_tasks[0],
        achievement_digest_task=achievement_tasks[1],
        achievement_top_week_task=achievement_tasks[2],
        checkin_prompts_task=checkin_tasks[0],
        checkin_reminders_task=checkin_tasks[1],
        streak_warnings_task=checkin_tasks[2],
        pulse_report_task=checkin_tasks[3],
        community_meeting_task=meeting_task,
    )


__all__ = ["TaskRegistry", "register_all_tasks"]
