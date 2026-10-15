from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest

from pybot.dto.community_checkins import PulseReportDTO, PulseReportRecipientDTO
from pybot.services.pulse_report import PulseReportService


def make_empty_report_service():
    db = AsyncMock()
    checkins = AsyncMock()
    attendance = AsyncMock()
    achievements = AsyncMock()
    notifications = AsyncMock()
    document_delivery = AsyncMock()
    technology_stack = AsyncMock()
    service = PulseReportService(
        db, checkins, attendance, achievements, notifications, document_delivery, technology_stack
    )
    report = PulseReportDTO(
        week_start=date(2026, 10, 12),
        week_end=date(2026, 10, 18),
        report_date=date(2026, 10, 15),
        invited_count=0,
        answered_count=0,
        needs_help_count=0,
        goals_achieved_count=0,
        visit_count=0,
        answered_delta=0,
        needs_help_delta=0,
        goals_achieved_delta=0,
        visits_delta=0,
        needs_help=(),
        answered_participants=(),
        achievements=(),
        non_responders=(),
        learning_technologies=(),
    )
    checkins.find_report_recipients.return_value = (PulseReportRecipientDTO(user_id=1, telegram_id=123),)
    return service, checkins, notifications, document_delivery, report


@pytest.mark.asyncio
async def test_empty_report_still_reaches_admin_as_text_when_pdf_rendering_fails(monkeypatch) -> None:
    from pybot.services import pulse_report

    service, _, notifications, document_delivery, report = make_empty_report_service()

    async def build_report(_service: PulseReportService, *, now: datetime | None = None) -> PulseReportDTO:
        del now
        return report

    def fail_render(report):
        raise RuntimeError("missing local native Pango")

    monkeypatch.setattr(PulseReportService, "build_report", build_report)
    monkeypatch.setattr(pulse_report, "render_pulse_report_pdf", fail_render)

    delivered = await service.deliver_weekly_report()

    assert delivered == 1
    document_delivery.send_pdf.assert_not_awaited()
    message = notifications.send_message.await_args.args[0].message
    assert "PDF-отчёт не собрался" in message
    assert "Ответили: 0/0" in message
    assert "Посещений: 0" in message


@pytest.mark.asyncio
async def test_empty_week_is_sent_as_pdf_when_renderer_succeeds(monkeypatch) -> None:
    from pybot.services import pulse_report

    service, _, notifications, document_delivery, report = make_empty_report_service()

    async def build_report(_service: PulseReportService, *, now: datetime | None = None) -> PulseReportDTO:
        del now
        return report

    monkeypatch.setattr(PulseReportService, "build_report", build_report)
    monkeypatch.setattr(pulse_report, "render_pulse_report_pdf", lambda _: b"%PDF-test")

    delivered = await service.deliver_weekly_report()

    assert delivered == 1
    document_delivery.send_pdf.assert_awaited_once()
    assert document_delivery.send_pdf.await_args.kwargs["content"] == b"%PDF-test"
    assert "0/0" in document_delivery.send_pdf.await_args.kwargs["caption"]
    notifications.send_message.assert_not_awaited()
    assert report.answered_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("pdf", (b"%PDF-test", None))
async def test_revoked_admin_role_prevents_pdf_and_fallback_delivery(pdf: bytes | None) -> None:
    service, checkins, notifications, document_delivery, report = make_empty_report_service()
    checkins.is_available_report_recipient.return_value = False
    recipient = PulseReportRecipientDTO(user_id=1, telegram_id=123)

    delivered = await service._deliver_to_admin(recipient, report, pdf, "caption", "fallback")

    assert delivered == 0
    checkins.is_available_report_recipient.assert_awaited_once_with(service._db, recipient)
    document_delivery.send_pdf.assert_not_awaited()
    notifications.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_report_counts_visits_for_last_seven_full_days_and_previous_seven_days() -> None:
    from pybot.core.config import YEKATERINBURG_TZ

    db = AsyncMock()
    checkins = AsyncMock()
    attendance = AsyncMock()
    achievements = AsyncMock()
    notifications = AsyncMock()
    document_delivery = AsyncMock()
    technology_stack = AsyncMock()
    checkins.find_campaign_rows.side_effect = [(), ()]
    checkins.find_consecutive_missed_weeks.return_value = {}
    attendance.find_visit_dates_for_users.return_value = {}
    attendance.find_visit_counts_for_period.return_value = {}
    attendance.count_visits_for_period.side_effect = [11, 7]
    achievements.find_awards_in_period.return_value = ()
    technology_stack.get_stacks_for_users.return_value = {}
    technology_stack.get_top_learning_technologies.return_value = ()
    service = PulseReportService(
        db,
        checkins,
        attendance,
        achievements,
        notifications,
        document_delivery,
        technology_stack,
    )

    report = await service.build_report(now=datetime(2026, 10, 15, 19, tzinfo=YEKATERINBURG_TZ))

    assert report.report_date == date(2026, 10, 15)
    assert report.visit_count == 11
    assert report.visits_delta == 4
    assert attendance.find_visit_counts_for_period.await_args.kwargs == {
        "start_date": date(2026, 10, 8),
        "end_date": date(2026, 10, 14),
        "user_ids": (),
    }
    assert [item.kwargs for item in attendance.count_visits_for_period.await_args_list] == [
        {"start_date": date(2026, 10, 8), "end_date": date(2026, 10, 14)},
        {"start_date": date(2026, 10, 1), "end_date": date(2026, 10, 7)},
    ]
