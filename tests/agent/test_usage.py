from decimal import Decimal
from unittest.mock import Mock

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RunUsage

import pybot.services.agent.usage as usage_module
from pybot.services.agent.usage import (
    log_agent_usage,
    snapshot_run_usage,
    summarize_run_usage,
    summarize_usage_increment,
)


def test_summarize_multiple_model_responses():
    usage = RunUsage(
        requests=2,
        input_tokens=180,
        cache_read_tokens=20,
        output_tokens=30,
        details={"reasoning_tokens": 5},
    )
    messages = [
        ModelResponse(
            parts=[TextPart("private prompt")],
            provider_details={
                "polza_usage": {
                    "input_tokens": 100,
                    "cached_tokens": 10,
                    "output_tokens": 15,
                    "reasoning_tokens": 2,
                    "cost_rub": "0.01",
                }
            },
        ),
        ModelResponse(
            parts=[TextPart("private answer")],
            provider_details={
                "polza_usage": {
                    "input_tokens": 80,
                    "cached_tokens": 10,
                    "output_tokens": 15,
                    "reasoning_tokens": 3,
                    "cost_rub": "0.02",
                }
            },
        ),
    ]

    summary = summarize_run_usage(usage, messages)

    assert summary.requests == 2
    assert summary.input_tokens == 180
    assert summary.cached_tokens == 20
    assert summary.output_tokens == 30
    assert summary.reasoning_tokens == 5
    assert summary.cost_rub == Decimal("0.03")


def test_missing_provider_usage_does_not_break_and_log_has_no_message_text(monkeypatch):
    private_text = "ФИО и вопрос пользователя"
    usage = RunUsage(requests=1, input_tokens=12, output_tokens=4)
    message = ModelResponse(parts=[TextPart(private_text)])
    log = Mock()
    monkeypatch.setattr(usage_module, "logger", log)

    summary = summarize_run_usage(usage, [message])
    log_agent_usage(summary, "deepseek/test-model")

    assert summary.input_tokens == 12
    assert summary.output_tokens == 4
    assert summary.cost_rub is None
    logged_text = str(log.info.call_args)
    assert private_text not in logged_text
    assert "event=agent_usage" in logged_text
    assert log.info.call_args.kwargs["cost_rub"] == "unknown"


def test_deferred_usage_logs_only_the_incremental_request():
    usage = RunUsage(requests=1, input_tokens=100, cache_read_tokens=10, output_tokens=20)
    previous = snapshot_run_usage(usage)
    usage.requests = 2
    usage.input_tokens = 150
    usage.cache_read_tokens = 30
    usage.output_tokens = 28
    usage.details["reasoning_tokens"] = 2
    messages = [
        ModelResponse(
            parts=[TextPart("private continuation")],
            provider_details={
                "polza_usage": {
                    "input_tokens": 50,
                    "cached_tokens": 20,
                    "output_tokens": 8,
                    "reasoning_tokens": 2,
                    "cost_rub": "0.004",
                }
            },
        )
    ]

    summary = summarize_usage_increment(usage, previous, messages)

    assert summary.requests == 1
    assert summary.input_tokens == 50
    assert summary.cached_tokens == 20
    assert summary.output_tokens == 8
    assert summary.reasoning_tokens == 2
    assert summary.cost_rub == Decimal("0.004")
