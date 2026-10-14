"""Port for extracting a participant's technology stack from free text."""

from typing import Protocol

from ...dto.technology_stack import ParsedTechnologyStackDTO


class TechnologyStackParserPort(Protocol):
    """Parse free-form Russian text into typed technology names and levels."""

    async def parse(self, text: str) -> ParsedTechnologyStackDTO:
        """Return structured stack entries without persisting them."""
        ...
