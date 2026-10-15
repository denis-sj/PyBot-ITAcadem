from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from pybot.core.config import YEKATERINBURG_TZ
from pybot.domain.services.community_checkins import CheckinStatus
from pybot.dto.community_checkins import (
    CheckinPromptRecipientDTO,
    ParsedCheckinResponseDTO,
    StreakWarningRecipientDTO,
)
from pybot.dto.technology_stack import TechnologyStackRecommendationDTO
from pybot.services import community_checkins as community_checkins_module
from pybot.services.community_checkins import CommunityCheckinService
from pybot.services.ports import NotificationBlockedError


def make_checkin_service():
    db = AsyncMock()
    repository = AsyncMock()
    attendance = AsyncMock()
    preferences = AsyncMock()
    goals = AsyncMock()
    stack = AsyncMock()
    parser = AsyncMock()
    notifications = AsyncMock()
    service = CommunityCheckinService(
        db,
        repository,
        attendance,
        preferences,
        goals,
        stack,
        parser,
        notifications,
    )
    return service, db, repository, attendance, preferences, goals, stack, parser, notifications


@pytest.mark.asyncio
async def test_response_stores_only_normalized_summary_and_replaces_previous_answer() -> None:
    service, db, repository, _, _, goals, stack, parser, notifications = make_checkin_service()
    repository.find_latest_prompted_checkin.return_value = SimpleNamespace(id=19, week_start=date(2026, 10, 12))
    parser.parse.return_value = ParsedCheckinResponseDTO(
        summary="Разбираюсь с Python",
        status=CheckinStatus.STUCK,
        help_topic="питон",
    )
    stack.find_helpers.return_value = TechnologyStackRecommendationDTO(technology="Python", helpers=())

    reply = await service.handle_response(
        42,
        "СЫРОЙ ТЕКСТ, КОТОРЫЙ НЕЛЬЗЯ ХРАНИТЬ",
        now=datetime(2026, 10, 15, 16, tzinfo=YEKATERINBURG_TZ),
    )

    assert reply is not None and reply.startswith("Записал, удачи на неделе!")
    saved = repository.save_response.await_args.kwargs["response"]
    assert saved.summary == "Разбираюсь с Python"
    assert saved.help_topic == "Python"
    assert "СЫРОЙ ТЕКСТ" not in str(repository.save_response.await_args)
    repository.save_response.assert_awaited_once()
    stack.find_helpers.assert_awaited_once_with(42, "Python")
    goals.complete_active_goal.assert_not_awaited()
    notifications.send_message.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_goal_completion_and_new_goal_are_applied_from_parsed_checkin() -> None:
    service, _, repository, _, _, goals, _, parser, _ = make_checkin_service()
    repository.find_latest_prompted_checkin.return_value = SimpleNamespace(id=20, week_start=date(2026, 10, 12))
    parser.parse.return_value = ParsedCheckinResponseDTO(
        summary="Завершил учебный проект",
        status=CheckinStatus.GOAL_ACHIEVED,
        new_goal="пройти собеседование",
    )

    await service.handle_response(42, "Достиг цели, теперь хочу пройти собеседование", now=_open_checkin_time())

    goals.complete_active_goal.assert_awaited_once_with(42)
    goals.set_goal.assert_awaited_once_with(42, "пройти собеседование")


@pytest.mark.asyncio
async def test_expired_checkin_does_not_consume_or_parse_message() -> None:
    service, _, repository, _, _, _, _, parser, _ = make_checkin_service()
    repository.find_latest_prompted_checkin.return_value = SimpleNamespace(id=21, week_start=date(2026, 10, 12))

    result = await service.handle_response(
        42,
        "Обычное сообщение",
        now=datetime(2026, 10, 21, 15, tzinfo=YEKATERINBURG_TZ),
    )

    assert result is None
    parser.parse.assert_not_awaited()
    repository.save_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_very_long_checkin_response_is_rejected_before_model_or_storage() -> None:
    service, db, repository, _, _, _, _, parser, _ = make_checkin_service()
    repository.find_latest_prompted_checkin.return_value = SimpleNamespace(id=22, week_start=date(2026, 10, 12))

    result = await service.handle_response(42, "x" * 20_000, now=_open_checkin_time())

    assert result == "Ответ слишком длинный. Сократи его до 4000 символов и отправь ещё раз."
    parser.parse.assert_not_awaited()
    repository.save_response.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkin_parse_failure_log_contains_no_user_or_message_data(monkeypatch) -> None:
    service, _, repository, _, _, _, _, parser, _ = make_checkin_service()
    repository.find_latest_prompted_checkin.return_value = SimpleNamespace(id=23, week_start=date(2026, 10, 12))
    parser.parse.side_effect = ValueError("PRIVATE-EXCEPTION-CONTENT")
    logger_mock = Mock()
    monkeypatch.setattr(community_checkins_module, "logger", logger_mock)

    await service.handle_response(
        8_888_123,
        "PRIVATE-CHECKIN-SOURCE",
        now=_open_checkin_time(),
    )

    logger_mock.warning.assert_called_once()
    logger_mock.exception.assert_not_called()
    assert "8_888_123" not in repr(logger_mock.warning.call_args)
    assert "PRIVATE-CHECKIN-SOURCE" not in repr(logger_mock.warning.call_args)
    assert "PRIVATE-EXCEPTION-CONTENT" not in repr(logger_mock.warning.call_args)


@pytest.mark.asyncio
async def test_prompt_403_marks_user_blocked_and_keeps_task_successful() -> None:
    service, db, repository, _, _, _, _, _, notifications = make_checkin_service()
    repository.find_prompt_recipients.return_value = (
        CheckinPromptRecipientDTO(
            user_id=42,
            telegram_id=420,
            first_name="Анна",
            goal_id=None,
            goal_text=None,
            has_technology_stack=False,
        ),
    )
    repository.claim_prompt.return_value = True
    notifications.send_message.side_effect = NotificationBlockedError("bot blocked")
    user = SimpleNamespace(bot_is_blocked=False)
    db.scalar.return_value = user

    sent_count = await service.deliver_weekly_prompts(now=datetime(2026, 10, 14, 15, tzinfo=YEKATERINBURG_TZ))

    assert sent_count == 0
    assert user.bot_is_blocked is True
    assert db.commit.await_count == 3


@pytest.mark.asyncio
async def test_streak_warning_is_sent_once_when_three_week_streak_has_no_current_visit() -> None:
    service, db, repository, attendance, preferences, _, _, _, notifications = make_checkin_service()
    recipient = StreakWarningRecipientDTO(user_id=42, telegram_id=420)
    repository.find_streak_warning_recipients.return_value = (recipient,)
    attendance.find_visit_dates_for_users.return_value = {
        42: [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)]
    }
    preferences.claim_streak_warning.return_value = True

    sent = await service.warn_at_risk_streaks(now=datetime(2026, 10, 24, 12, tzinfo=YEKATERINBURG_TZ))

    assert sent == 1
    preferences.claim_streak_warning.assert_awaited_once_with(
        db,
        user_id=42,
        week_start=date(2026, 10, 19),
    )
    assert "3 недель" in notifications.send_message.await_args.args[0].message


def _open_checkin_time() -> datetime:
    return datetime(2026, 10, 15, 16, tzinfo=YEKATERINBURG_TZ)
