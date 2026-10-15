"""Port for extracting a concise, typed weekly check-in summary."""

from typing import Protocol

from ...dto.community_checkins import ParsedCheckinResponseDTO


class CheckinResponseParserPort(Protocol):
    """Extract only report fields from one transient participant message."""

    async def parse(self, text: str) -> ParsedCheckinResponseDTO:
        """Return a bounded summary, status, help topic and optional new goal."""
        ...
