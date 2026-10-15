"""Non-delivering pulse report adapter for development and tests."""

from ...core import logger
from ...services.ports.pulse_report_delivery import PulseReportDeliveryPort


class LoggingPulseReportDelivery(PulseReportDeliveryPort):
    """Log document metadata without sending or logging report contents."""

    async def send_pdf(self, *, recipient_id: int, filename: str, content: bytes, caption: str) -> None:
        logger.info(
            "событие=pulse_report_delivery backend=logging filename={filename} bytes={size}",
            filename=filename,
            size=len(content),
        )
