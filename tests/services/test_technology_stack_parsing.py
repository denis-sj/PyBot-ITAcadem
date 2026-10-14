from unittest.mock import AsyncMock

import pytest

from pybot.domain.services.technology_stack import TechnologyLevel, TechnologyStackValidationError
from pybot.dto.technology_stack import ParsedTechnologyStackDTO, TechnologyStackItemDTO
from pybot.services.technology_stack_parsing import TechnologyStackParsingService


@pytest.mark.asyncio
async def test_parser_canonicalizes_aliases_and_removes_case_insensitive_duplicates() -> None:
    parser = AsyncMock()
    parser.parse.return_value = ParsedTechnologyStackDTO(
        items=(
            TechnologyStackItemDTO(technology=" питон ", level=TechnologyLevel.KNOW),
            TechnologyStackItemDTO(technology="PYTHON", level=TechnologyLevel.LEARNING),
            TechnologyStackItemDTO(technology="rust", level=TechnologyLevel.KNOW),
        )
    )
    service = TechnologyStackParsingService(parser)

    items = await service.parse("Знаю Python, изучаю Rust")

    assert items == (
        TechnologyStackItemDTO(technology="Python", level=TechnologyLevel.LEARNING),
        TechnologyStackItemDTO(technology="Rust", level=TechnologyLevel.KNOW),
    )
    parser.parse.assert_awaited_once_with("Знаю Python, изучаю Rust")


@pytest.mark.asyncio
async def test_parser_rejects_garbage_when_model_returns_no_technologies() -> None:
    parser = AsyncMock()
    parser.parse.return_value = ParsedTechnologyStackDTO(items=())
    service = TechnologyStackParsingService(parser)

    with pytest.raises(TechnologyStackValidationError):
        await service.parse("Сегодня хорошая погода")
