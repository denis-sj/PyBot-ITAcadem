"""Opt-in live-model evaluations with synthetic identities and no database access."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import httpx2
from fixtures import EvalReads
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits

from pybot.core.config import get_settings
from pybot.core.constants import PointsTypeEnum
from pybot.dto import UserReadDTO
from pybot.dto.agent import AgentConversation
from pybot.dto.value_objects import Points
from pybot.infrastructure.adapters.polza_agent import PolzaAgentModel
from pybot.services.agent.service import AgentService, build_agent_instructions
from pybot.services.agent.tools import AgentDependencies, AgentToolRegistry
from pybot.services.user_services.users import UserService


class EvalCase(BaseModel):
    name: str
    question: str
    roles: frozenset[str]
    expected_tools: set[str]
    required_phrases: list[str]
    forbidden_phrases: list[str]


class EvalUsers(UserService):
    """Only the synthetic eval actor exists; production repositories are never used."""

    def __init__(self, roles: frozenset[str]) -> None:
        self.roles = roles

    async def find_user_by_telegram_id(self, tg_id: int) -> UserReadDTO | None:
        if tg_id != 1:
            return None
        return UserReadDTO(
            id=1,
            telegram_id=1,
            first_name="Тест",
            last_name=None,
            patronymic=None,
            join_date=date(2026, 1, 1),
            academic_points=Points(value=0, point_type=PointsTypeEnum.ACADEMIC),
            reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        )

    async def find_all_user_roles(self, user_id: int) -> set[str] | None:
        return set(self.roles) if user_id == 1 else None


async def run_evals() -> int:
    """Run paid model calls only through this explicitly invoked CLI."""
    settings = get_settings()
    cases = TypeAdapter(list[EvalCase]).validate_json(
        Path(__file__).with_name("cases.json").read_text(encoding="utf-8")
    )
    failures = 0
    async with httpx2.AsyncClient(timeout=settings.agent_timeout_s) as client:
        model = PolzaAgentModel(settings, client).get_model()
        for case in cases:
            dependencies = AgentDependencies(
                AgentConversation(chat_id=1, telegram_id=1), EvalUsers(case.roles), settings, EvalReads()
            )
            agent = Agent(
                model,
                deps_type=AgentDependencies,
                output_type=[str, DeferredToolRequests],
                instructions=build_agent_instructions(),
                toolsets=[AgentToolRegistry().for_roles(case.roles)],
                model_settings={"temperature": 0, "max_tokens": 1000, "parallel_tool_calls": False},
            )
            try:
                async with asyncio.timeout(settings.agent_timeout_s):
                    result = await agent.run(
                        case.question,
                        deps=dependencies,
                        usage_limits=UsageLimits(request_limit=settings.agent_max_steps, tool_calls_limit=8),
                    )
                if not isinstance(result.output, str):
                    print(f"{case.name}: FAIL unexpected_deferred_output")
                    failures += 1
                    continue
                text = AgentService._validate_numbers(result.output, result.new_messages()).lower()
                tools = {
                    part.tool_name
                    for message in result.new_messages()
                    if isinstance(message, ModelResponse)
                    for part in message.parts
                    if isinstance(part, ToolCallPart)
                }
                passed = (
                    case.expected_tools == tools
                    and all(phrase.lower() in text for phrase in case.required_phrases)
                    and not any(phrase.lower() in text for phrase in case.forbidden_phrases)
                )
                print(f"{case.name}: {'PASS' if passed else 'FAIL'} tools={','.join(sorted(tools))}")
                failures += not passed
            except Exception as error:
                # Never print provider error bodies or credentials.
                print(f"{case.name}: FAIL error_type={type(error).__name__}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_evals()))
