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
