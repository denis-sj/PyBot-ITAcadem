"""Bound model requests and their transient retries; application tools run separately."""

import asyncio
import random

import httpx2
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, ModelSettings
from pydantic_ai.models.wrapper import WrapperModel

from ...core import logger

_TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})
_MAX_MODEL_RETRIES = 2


class AgentTimeoutModel(WrapperModel):
    """Retry only model HTTP calls, sharing one AGENT_TIMEOUT_S deadline per request."""

    def __init__(self, wrapped: Model, timeout_seconds: float) -> None:
        super().__init__(wrapped)
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with asyncio.timeout(self.timeout_seconds):
            for attempt in range(_MAX_MODEL_RETRIES + 1):
                try:
                    return await self.wrapped.request(messages, model_settings, model_request_parameters)
                except Exception as error:
                    status_code = _transient_status(error)
                    if status_code is None or attempt == _MAX_MODEL_RETRIES:
                        raise
                    logger.warning(
                        "event=agent_model_retry attempt={attempt} status_code={status_code}",
                        attempt=attempt + 1,
                        status_code=status_code,
                    )
                    await asyncio.sleep(random.uniform(0.5, 1.5))  # noqa: S311 - non-security retry jitter
        raise RuntimeError("Model retry loop ended without a response")


def _transient_status(error: Exception) -> int | str | None:
    if isinstance(error, ModelHTTPError) and error.status_code in _TRANSIENT_HTTP_STATUSES:
        return error.status_code
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, httpx2.ConnectTimeout):
            return "connection_timeout"
        cause = cause.__cause__
    return None
