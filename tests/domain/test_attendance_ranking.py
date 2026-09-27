"""Unit tests for AttendanceRankingCalculator."""

from __future__ import annotations

import pytest

from pybot.domain.services.attendance_ranking import (
    AttendanceRankingCalculator,
    ParticipantAttendanceStat,
)


@pytest.fixture
def calculator() -> AttendanceRankingCalculator:
    return AttendanceRankingCalculator()


def test_empty_participants_returns_empty_list(calculator: AttendanceRankingCalculator) -> None:
    assert calculator.calculate_rankings([]) == []


def test_participants_without_minutes_or_visits_are_excluded(
    calculator: AttendanceRankingCalculator,
) -> None:
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="Иванов И.", total_minutes=0, visits_count=1),
        ParticipantAttendanceStat(user_id=2, user_full_name="Петров П.", total_minutes=60, visits_count=0),
        ParticipantAttendanceStat(user_id=3, user_full_name="Сидоров С.", total_minutes=0, visits_count=0),
        ParticipantAttendanceStat(user_id=4, user_full_name="Активный А.", total_minutes=90, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert len(rankings) == 1
    assert rankings[0].user_id == 4
    assert rankings[0].rank == 1
    assert rankings[0].total_minutes == 90
    assert rankings[0].visits_count == 1


def test_single_active_participant(calculator: AttendanceRankingCalculator) -> None:
    stats = [
        ParticipantAttendanceStat(user_id=10, user_full_name="Единственный Е.", total_minutes=120, visits_count=2),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert len(rankings) == 1
    assert rankings[0].rank == 1
    assert rankings[0].user_id == 10
    assert rankings[0].total_minutes == 120
    assert rankings[0].visits_count == 2


def test_distinct_minutes_strict_ordering(calculator: AttendanceRankingCalculator) -> None:
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="Первый П.", total_minutes=300, visits_count=3),
        ParticipantAttendanceStat(user_id=2, user_full_name="Второй В.", total_minutes=200, visits_count=2),
        ParticipantAttendanceStat(user_id=3, user_full_name="Третий Т.", total_minutes=100, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 2, 3]
    assert [r.user_id for r in rankings] == [1, 2, 3]


def test_competition_ranking_1_2_2_4(calculator: AttendanceRankingCalculator) -> None:
    """Standard competition ranking with ties in the middle: 1, 2, 2, 4."""
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="Лидер Л.", total_minutes=150, visits_count=2),
        ParticipantAttendanceStat(user_id=2, user_full_name="Борис Б.", total_minutes=100, visits_count=2),
        ParticipantAttendanceStat(user_id=3, user_full_name="Антон А.", total_minutes=100, visits_count=1),
        ParticipantAttendanceStat(user_id=4, user_full_name="Денис Д.", total_minutes=50, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 2, 2, 4]
    assert rankings[0].user_id == 1
    # Tied participants Anton and Boris both receive rank 2
    assert rankings[1].rank == 2
    assert rankings[2].rank == 2
    # Next participant receives rank 4 (rank 3 is skipped)
    assert rankings[3].user_id == 4
    assert rankings[3].rank == 4


def test_competition_ranking_shared_first_place(calculator: AttendanceRankingCalculator) -> None:
    """Two participants share 1st place, next gets 3rd place: 1, 1, 3."""
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="Алексей А.", total_minutes=120, visits_count=1),
        ParticipantAttendanceStat(user_id=2, user_full_name="Владимир В.", total_minutes=120, visits_count=2),
        ParticipantAttendanceStat(user_id=3, user_full_name="Сергей С.", total_minutes=60, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 1, 3]


def test_competition_ranking_three_shared_first_place(calculator: AttendanceRankingCalculator) -> None:
    """Three participants share 1st place, next gets 4th: 1, 1, 1, 4."""
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="А А.", total_minutes=200, visits_count=2),
        ParticipantAttendanceStat(user_id=2, user_full_name="Б Б.", total_minutes=200, visits_count=3),
        ParticipantAttendanceStat(user_id=3, user_full_name="В В.", total_minutes=200, visits_count=1),
        ParticipantAttendanceStat(user_id=4, user_full_name="Г Г.", total_minutes=50, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 1, 1, 4]


def test_all_participants_tied(calculator: AttendanceRankingCalculator) -> None:
    """All participants have identical flight time."""
    stats = [
        ParticipantAttendanceStat(user_id=1, user_full_name="А А.", total_minutes=100, visits_count=1),
        ParticipantAttendanceStat(user_id=2, user_full_name="Б Б.", total_minutes=100, visits_count=2),
        ParticipantAttendanceStat(user_id=3, user_full_name="В В.", total_minutes=100, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 1, 1]


def test_deterministic_tie_sorting_by_name_and_id(calculator: AttendanceRankingCalculator) -> None:
    """When scores are tied, presentation order is alphabetical by name, then user_id."""
    stats = [
        ParticipantAttendanceStat(user_id=10, user_full_name="Яков Я.", total_minutes=100, visits_count=1),
        ParticipantAttendanceStat(user_id=5, user_full_name="Борис Б.", total_minutes=100, visits_count=1),
        ParticipantAttendanceStat(user_id=2, user_full_name="Борис Б.", total_minutes=100, visits_count=1),
        ParticipantAttendanceStat(user_id=1, user_full_name="Анна А.", total_minutes=100, visits_count=1),
    ]
    rankings = calculator.calculate_rankings(stats)

    assert [r.rank for r in rankings] == [1, 1, 1, 1]
    assert [r.user_id for r in rankings] == [1, 2, 5, 10]
