"""Transport boundary for generated community pulse report documents."""

from typing import Protocol


class PulseReportDeliveryPort(Protocol):
    """Send one rendered report PDF to a Telegram administrator."""

    async def send_pdf(self, *, recipient_id: int, filename: str, content: bytes, caption: str) -> None:
        """Deliver the PDF document with its human-readable summary caption."""
        ...
