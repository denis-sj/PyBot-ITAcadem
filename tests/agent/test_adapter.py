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


@pytest.mark.asyncio
async def test_deepseek_wire_omits_unsupported_parallel_parameter(agent_settings, monkeypatch):
    import json

    from pydantic_ai import models
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.tools import ToolDefinition

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    agent_settings.polza_api_key = SecretStr("synthetic-key")
    agent_settings.agent_model = "deepseek/deepseek-v4.1-flash"
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 1,
                "model": agent_settings.agent_model,
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Готово"}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = PolzaAgentModel(agent_settings, client).get_model()
        await model.request(
            [ModelRequest(parts=[UserPromptPart("synthetic")])],
            {"max_tokens": 1000, "parallel_tool_calls": False},
            ModelRequestParameters(
                function_tools=[
                    ToolDefinition(
                        name="get_my_profile",
                        parameters_json_schema={"type": "object", "properties": {}},
                    )
                ]
            ),
        )
    assert len(requests) == 1
    assert "parallel_tool_calls" not in requests[0]
    assert requests[0]["provider"]["only"] == ["decart/fp4"]
    assert requests[0]["provider"]["require_parameters"] is True
    assert requests[0]["tools"][0]["function"]["name"] == "get_my_profile"
