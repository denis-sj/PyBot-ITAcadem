import httpx2
import pytest
from pydantic import SecretStr

from pybot.infrastructure.adapters.polza_agent import PolzaAgentModel


@pytest.mark.asyncio
async def test_polza_agent_constructs_chat_model_independently_of_vision(agent_settings):
    agent_settings.polza_api_key = SecretStr("synthetic-key")
    agent_settings.agent_model = "google/gemini-3.5-flash-lite"
    agent_settings.polza_vision_model = "vision-model-unchanged"
    async with httpx2.AsyncClient() as client:
        adapter = PolzaAgentModel(agent_settings, client)
        model = adapter.get_model()
        assert model.model_name == "google/gemini-3.5-flash-lite"
        assert agent_settings.polza_vision_model == "vision-model-unchanged"


@pytest.mark.asyncio
async def test_missing_key_does_not_create_provider(agent_settings):
    agent_settings.polza_api_key = None
    async with httpx2.AsyncClient() as client:
        with pytest.raises(ValueError, match="POLZA_API_KEY"):
            PolzaAgentModel(agent_settings, client).get_model()
