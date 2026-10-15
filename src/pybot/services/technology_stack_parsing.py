"""Application boundary for stack parsing and deterministic canonicalization."""

from ..core.constants import MAX_MODEL_INPUT_CHARACTERS
from ..domain.services.technology_stack import (
    TechnologyStackItem,
    TechnologyStackValidationError,
    normalize_technology_stack,
)
from ..dto.technology_stack import TechnologyStackItemDTO
from .ports.technology_stack_parser import TechnologyStackParserPort


class TechnologyStackParsingService:
    """Parse a draft with the model, then enforce code-owned names and limits."""

    def __init__(self, parser: TechnologyStackParserPort) -> None:
        self._parser = parser

    async def parse(self, text: str) -> tuple[TechnologyStackItemDTO, ...]:
        if len(text) > MAX_MODEL_INPUT_CHARACTERS:
            raise TechnologyStackValidationError(
                f"Сообщение слишком длинное. Сократите его до {MAX_MODEL_INPUT_CHARACTERS} символов."
            )
        parsed_stack = await self._parser.parse(text)
        normalized_items = normalize_technology_stack(
            tuple(TechnologyStackItem(item.technology, item.level) for item in parsed_stack.items)
        )
        return tuple(TechnologyStackItemDTO(technology=item.technology, level=item.level) for item in normalized_items)
