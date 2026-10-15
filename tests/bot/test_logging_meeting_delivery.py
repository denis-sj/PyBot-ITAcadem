import pytest

from pybot.infrastructure.ports import logging_meeting_delivery as logging_module
from pybot.infrastructure.ports.logging_meeting_delivery import LoggingMeetingDelivery


@pytest.mark.asyncio
async def test_meeting_delivery_logs_only_event_types(mocker) -> None:
    service = LoggingMeetingDelivery()
    info_mock = mocker.patch.object(logging_module.logger, "info")

    await service.send_announcement(123456, 91, "PRIVATE-MEETING-TITLE", 81)
    await service.send_invitation(987654321, "PRIVATE-MEETING-GOAL", 82)
    await service.send_text(1122334455, "PRIVATE-MEETING-PLACE")

    assert info_mock.call_count == 3
    logged_calls = repr(info_mock.call_args_list)
    assert "123456" not in logged_calls
    assert "91" not in logged_calls
    assert "81" not in logged_calls
    assert "987654321" not in logged_calls
    assert "1122334455" not in logged_calls
    assert "PRIVATE-MEETING" not in logged_calls
