"""Application boundary for stack parsing and deterministic canonicalization."""

from ..domain.services.technology_stack import TechnologyStackItem, normalize_technology_stack
from ..dto.technology_stack import TechnologyStackItemDTO
from .ports.technology_stack_parser import TechnologyStackParserPort


class TechnologyStackParsingService:
    """Parse a draft with the model, then enforce code-owned names and limits."""

    def __init__(self, parser: TechnologyStackParserPort) -> None:
        self._parser = parser

    async def parse(self, text: str) -> tuple[TechnologyStackItemDTO, ...]:
        parsed_stack = await self._parser.parse(text)
        normalized_items = normalize_technology_stack(
            tuple(TechnologyStackItem(item.technology, item.level) for item in parsed_stack.items)
        )
        return tuple(TechnologyStackItemDTO(technology=item.technology, level=item.level) for item in normalized_items)
