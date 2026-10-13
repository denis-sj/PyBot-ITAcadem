"""Aggregate model usage for privacy-safe agent telemetry."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.usage import RunUsage

from ...core import logger


@dataclass(frozen=True)
class AgentUsageSummary:
    """Usage totals for one agent run or a resumed portion of one."""

    requests: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_rub: Decimal | None


def summarize_run_usage(usage: RunUsage, messages: Sequence[ModelMessage]) -> AgentUsageSummary:
    """Combine Pydantic AI token totals with Polza's exact response cost."""
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    provider_usage = _summarize_provider_usage(responses, usage.requests)
    counters = _usage_counters(usage)
    if provider_usage is not None:
        counters = provider_usage

    return AgentUsageSummary(
        requests=counters.requests,
        input_tokens=counters.input_tokens,
        cached_tokens=counters.cached_tokens,
        output_tokens=counters.output_tokens,
        reasoning_tokens=counters.reasoning_tokens,
        cost_rub=provider_usage.cost_rub if provider_usage is not None else None,
    )


def snapshot_run_usage(usage: RunUsage) -> AgentUsageSummary:
    """Capture cumulative counters before a deferred action resumes."""
    return _usage_counters(usage)


def summarize_usage_increment(
    usage: RunUsage,
    previous: AgentUsageSummary,
    messages: Sequence[ModelMessage],
) -> AgentUsageSummary:
    """Measure usage added by a deferred-action continuation."""
    request_count = usage.requests - previous.requests
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    provider_usage = _summarize_provider_usage(responses, request_count)
    counters = _usage_counters(usage)
    if provider_usage is not None:
        return provider_usage
    return AgentUsageSummary(
        requests=request_count,
        input_tokens=counters.input_tokens - previous.input_tokens,
        cached_tokens=counters.cached_tokens - previous.cached_tokens,
        output_tokens=counters.output_tokens - previous.output_tokens,
        reasoning_tokens=counters.reasoning_tokens - previous.reasoning_tokens,
        cost_rub=None,
    )


def log_agent_usage(summary: AgentUsageSummary, model: str) -> None:
    """Write numeric-only run telemetry without prompts, answers, or tool arguments."""
    cost_rub = "unknown" if summary.cost_rub is None else format(summary.cost_rub, "f")
    logger.info(
        "event=agent_usage requests={requests} input_tokens={input_tokens} cached_tokens={cached_tokens} "
        "output_tokens={output_tokens} reasoning_tokens={reasoning_tokens} cost_rub={cost_rub} model={model}",
        requests=summary.requests,
        input_tokens=summary.input_tokens,
        cached_tokens=summary.cached_tokens,
        output_tokens=summary.output_tokens,
        reasoning_tokens=summary.reasoning_tokens,
        cost_rub=cost_rub,
        model=model,
    )


def _polza_usage(response: ModelResponse) -> Mapping[str, int | str] | None:
    details = response.provider_details
    if details is None:
        return None
    usage = details.get("polza_usage")
    if not isinstance(usage, Mapping):
        return None
    return usage


def _summarize_provider_usage(responses: Sequence[ModelResponse], requests: int) -> AgentUsageSummary | None:
    if requests == 0:
        return AgentUsageSummary(0, 0, 0, 0, 0, Decimal(0))
    provider_usages = [_polza_usage(response) for response in responses]
    if len(provider_usages) != requests or any(usage is None for usage in provider_usages):
        return None
    complete_usages = [usage for usage in provider_usages if usage is not None]
    costs = [_decimal_cost(usage["cost_rub"]) for usage in complete_usages if "cost_rub" in usage]
    if len(costs) == requests and all(cost is not None for cost in costs):
        cost_rub = sum((cost for cost in costs if cost is not None), start=Decimal(0))
    else:
        cost_rub = None
    return AgentUsageSummary(
        requests=requests,
        input_tokens=sum(_int_value(usage, "input_tokens") for usage in complete_usages),
        cached_tokens=sum(_int_value(usage, "cached_tokens") for usage in complete_usages),
        output_tokens=sum(_int_value(usage, "output_tokens") for usage in complete_usages),
        reasoning_tokens=sum(_int_value(usage, "reasoning_tokens") for usage in complete_usages),
        cost_rub=cost_rub,
    )


def _usage_counters(usage: RunUsage) -> AgentUsageSummary:
    return AgentUsageSummary(
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cache_read_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.details.get("reasoning_tokens", 0),
        cost_rub=None,
    )


def _int_value(usage: Mapping[str, int | str], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def _decimal_cost(value: int | str) -> Decimal | None:
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None
