import httpx2
import pytest
from openai.types.chat import ChatCompletion
from pydantic import SecretStr
from pydantic_ai.models.openai import OpenAIChatModel

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


@pytest.mark.asyncio
async def test_polza_usage_cost_is_preserved_as_provider_detail(agent_settings):
    agent_settings.polza_api_key = SecretStr("synthetic-key")
    async with httpx2.AsyncClient() as client:
        model = PolzaAgentModel(agent_settings, client).get_model()
    assert isinstance(model, OpenAIChatModel)
    response = ChatCompletion.model_validate(
        {
            "id": "synthetic-completion",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek/test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "synthetic"},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 12,
                "total_tokens": 112,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 3},
                "cost_rub": "0.0042",
            },
        }
    )

    details = model._process_provider_details(response)

    assert details is not None
    assert details["polza_usage"] == {
        "input_tokens": 100,
        "cached_tokens": 40,
        "output_tokens": 12,
        "reasoning_tokens": 3,
        "cost_rub": "0.0042",
    }

    alias_payload = response.model_dump()
    alias_payload["usage"] = {
        "prompt_tokens": 50,
        "completion_tokens": 8,
        "total_tokens": 58,
        "prompt_cache_hit_tokens": 15,
        "reasoning_tokens": 2,
        "cost": "0.005",
    }
    alias_response = ChatCompletion.model_validate(alias_payload)
    alias_details = model._process_provider_details(alias_response)

    assert alias_details is not None
    assert alias_details["polza_usage"] == {
        "input_tokens": 50,
        "cached_tokens": 15,
        "output_tokens": 8,
        "reasoning_tokens": 2,
        "cost_rub": "0.005",
    }
