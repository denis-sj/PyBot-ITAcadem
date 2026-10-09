"""Polza model adapter; attendance vision configuration stays independent."""

import httpx2
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...core.config import AppSettings


class PolzaAgentModel:
    """Build Pydantic AI's OpenAI chat model with the shared Polza credentials."""

    def __init__(self, settings: AppSettings, client: httpx2.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def get_model(self) -> Model:
        """Create the model lazily so a disabled agent needs no API key."""
        key = self.settings.polza_api_key
        if key is None or not key.get_secret_value().strip():
            raise ValueError("POLZA_API_KEY is required for the agent")
        return OpenAIChatModel(
            self.settings.agent_model,
            provider=OpenAIProvider(
                base_url=self.settings.polza_base_url,
                api_key=key.get_secret_value(),
                http_client=self.client,
            ),
        )
