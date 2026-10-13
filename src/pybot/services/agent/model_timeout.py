"""Apply the agent timeout to model requests, leaving application tools unbounded by it."""

import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, ModelSettings
from pydantic_ai.models.wrapper import WrapperModel


class AgentTimeoutModel(WrapperModel):
    """Wrap a model so only its network request is subject to AGENT_TIMEOUT_S."""

    def __init__(self, wrapped: Model, timeout_seconds: int) -> None:
        super().__init__(wrapped)
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with asyncio.timeout(self.timeout_seconds):
            return await self.wrapped.request(messages, model_settings, model_request_parameters)
