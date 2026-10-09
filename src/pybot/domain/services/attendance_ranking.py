"""Domain service for calculating attendance rankings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class ParticipantAttendanceStat:
    """Monthly attendance statistics for a single participant."""

    user_id: int
    user_full_name: str
    total_minutes: int
    visits_count: int


@dataclass(frozen=True, slots=True)
class AttendanceRankedParticipant:
    """Ranked participant result from competition ranking."""

    rank: int
    user_id: int
    user_full_name: str
    total_minutes: int
    visits_count: int


class AttendanceRankingCalculator:
    """Calculator for participant monthly attendance competition ranking.

    Implements standard competition ranking (1, 2, 2, 4) according to Section 5.1:
    - Metric: sum of minutes of confirmed visits for the calendar month (total_minutes).
    - Only active participants with total_minutes > 0 and visits_count > 0 are ranked.
    - Ordered by total_minutes descending.
    - Tied participants share the same rank; subsequent rank skips by the size of the tied group.
    - Secondary sorting (name asc, user_id asc) provides deterministic display order.
    """

    def calculate_rankings(
        self,
        participants: Sequence[ParticipantAttendanceStat],
    ) -> list[AttendanceRankedParticipant]:
        """Calculate competition rankings for a sequence of participant statistics.

        Args:
            participants: Raw participant statistics for the month.

        Returns:
            list[AttendanceRankedParticipant]: Ranked active participants.
        """
        # Filter: only participants with visits > 0 and total_minutes > 0
        active = [p for p in participants if p.visits_count > 0 and p.total_minutes > 0]
        if not active:
            return []

        # Sort: primary total_minutes desc, secondary name asc, user_id asc
        sorted_participants = sorted(
            active,
            key=lambda p: (-p.total_minutes, p.user_full_name.lower(), p.user_id),
        )

        ranked: list[AttendanceRankedParticipant] = []
        current_rank = 1
        previous_minutes: int | None = None

        for idx, p in enumerate(sorted_participants, start=1):
            if previous_minutes is not None and p.total_minutes == previous_minutes:
                rank = current_rank
            else:
                rank = idx
                current_rank = rank
                previous_minutes = p.total_minutes

            ranked.append(
                AttendanceRankedParticipant(
                    rank=rank,
                    user_id=p.user_id,
                    user_full_name=p.user_full_name,
                    total_minutes=p.total_minutes,
                    visits_count=p.visits_count,
                )
            )

        return ranked
