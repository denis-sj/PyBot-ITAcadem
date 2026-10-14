"""Structured Pydantic AI adapter for parsing participant stack messages."""

from pydantic_ai import Agent

from ...dto.technology_stack import ParsedTechnologyStackDTO
from ...services.ports.agent import AgentModelPort
from ...services.ports.technology_stack_parser import TechnologyStackParserPort

_PARSER_INSTRUCTIONS = """Разбери сообщение участника в список технологий и уровней.
Учитывай только технологии, прямо названные участником, и не добавляй выдуманные.
Для «знаю», «умею», «работал с» выбирай уровень know.
Для «изучаю», «учу», «осваиваю», «хочу разобраться» выбирай learning.
Если уровень из контекста непонятен, не включай эту технологию.
Сохраняй название технологии так, как его написал участник; канонизацию выполнит приложение.
Не считай обычный разговор о боте, меню, регистрации или постороннюю просьбу описанием стека.
Если технологий нет или сообщение не по теме, верни пустой список."""


class PydanticAITechnologyStackParser(TechnologyStackParserPort):
    """Use the configured agent model only for extraction, not decisions or persistence."""

    def __init__(self, model: AgentModelPort) -> None:
        self._model = model

    async def parse(self, text: str) -> ParsedTechnologyStackDTO:
        agent = Agent[None, ParsedTechnologyStackDTO](
            self._model.get_model(),
            output_type=ParsedTechnologyStackDTO,
            instructions=_PARSER_INSTRUCTIONS,
            model_settings={"temperature": 0, "max_tokens": 500},
        )
        result = await agent.run(text)
        return result.output
