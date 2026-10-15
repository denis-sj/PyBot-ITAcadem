"""Automatic coverage for the metadata that drives agent tool registration and evals."""

import json
from pathlib import Path

import pytest
import tiktoken
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from pybot.domain.agent import AgentAccessDeniedError
from pybot.services.agent.dependencies import AgentDependencies
from pybot.services.agent.tools import (
    AgentToolRegistry,
    ConfiguredAgentRoles,
)
from pybot.services.ports.agent import AgentActionsPort
from evals.agent.run import ACCEPTANCE_BASELINE_PATH, PREFIX_TOKEN_BUDGETS_PATH, _load_cases, _role_prefix_token_counts
from tests.agent.conftest import build_service


def _registry() -> AgentToolRegistry:
    return AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))


def _resolved_roles(registry: AgentToolRegistry, entry) -> frozenset[str]:
    roles = entry.metadata.roles
    return registry.broadcast_allowed_roles if isinstance(roles, ConfiguredAgentRoles) else roles


def test_each_tool_has_complete_bounded_metadata() -> None:
    registry = _registry()
    encoding = tiktoken.get_encoding("cl100k_base")

    assert len(registry.entries) == 39
    for entry in registry.entries:
        metadata = entry.metadata
        assert metadata.name == entry.tool.name
        assert metadata.description == entry.tool.description
        assert len(encoding.encode(metadata.description)) <= 60
        assert metadata.service
        assert metadata.roles
        assert metadata.kind in {"read", "action"}
        assert metadata.requires_confirmation is entry.tool.requires_approval
        if metadata.kind == "action":
            assert metadata.requires_confirmation
            assert entry.confirmation_summary is not None
            service_name, _, method_name = metadata.service.partition(".")
            assert service_name == "AgentActionsPort"
            assert method_name and hasattr(AgentActionsPort, method_name)
        else:
            assert not metadata.requires_confirmation


def test_every_tool_has_two_allowed_cases_and_one_rights_denial_case() -> None:
    registry = _registry()
    cases = _load_cases(None)
    registered_names = {entry.metadata.name for entry in registry.entries}

    assert all(case.expected_tools <= registered_names for case in cases)
    assert all(case.forbidden_tools <= registered_names for case in cases)
    for entry in registry.entries:
        name = entry.metadata.name
        positive_cases = [case for case in cases if name in case.expected_tools]
        negative_cases = [
            case
            for case in cases
            if case.security and name in case.forbidden_tools and name not in registry.for_roles(case.roles).policies
        ]
        assert len(positive_cases) >= 2, name
        assert negative_cases, name
        assert all(name in registry.for_roles(case.roles).policies for case in positive_cases), name


@pytest.mark.asyncio
async def test_each_registered_tool_rechecks_roles_before_invocation(agent_settings, conversation) -> None:
    registry = _registry()
    service, users, _ = build_service(agent_settings)
    users.find_all_user_roles.return_value = {"Unassigned"}
    dependencies = AgentDependencies(conversation, users, agent_settings, service.reads, service.actions)
    context = RunContext(deps=dependencies, model=TestModel(), usage=RunUsage())

    for entry in registry.entries:
        allowed_role = next(iter(_resolved_roles(registry, entry)))
        toolset = registry.for_roles(frozenset({allowed_role}))
        prepared_tools = await toolset.get_tools(context)
        with pytest.raises(AgentAccessDeniedError):
            await toolset.call_tool(
                entry.metadata.name,
                {},
                context,
                prepared_tools[entry.metadata.name],
            )


def test_role_prefix_stays_within_checked_in_budget() -> None:
    budgets = json.loads(PREFIX_TOKEN_BUDGETS_PATH.read_text(encoding="utf-8"))["roles"]
    counts = _role_prefix_token_counts()

    assert set(budgets) == set(counts)
    for role, budget in budgets.items():
        assert counts[role]["combined_tokens"] <= budget, role
        assert counts[role]["combined_tokens"] < budget * 0.95, role


def test_acceptance_baseline_records_legacy_thresholds_and_stage_b_deviation() -> None:
    baseline = json.loads(ACCEPTANCE_BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["legacy_case_count"] == 39
    assert len(baseline["legacy_case_names"]) == 39
    assert baseline["minimum_legacy_correct_tools_pct"] == 94.8718
    assert baseline["baseline_exact_cost_per_question_rub"] == "0.027"
    assert baseline["maximum_exact_cost_per_question_rub"] == "0.029700"
    assert baseline["baseline_cache_share_pct"] == 84.48
    assert baseline["minimum_cache_share_pct"] == 85
    assert baseline["accepted_baseline_deviation"] == {
        "stage": "B",
        "metric": "cache_share_pct",
        "observed_pct": 84.48,
        "threshold_pct": 85.0,
        "deviation_percentage_points": -0.52,
        "accepted": True,
    }
    assert baseline["maximum_reasoning_tokens"] == 0
    assert baseline["smoke_max_tool_selection_misses"] == 1
    assert baseline["smoke_max_security_errors"] == 0
    assert Path("evals/agent/reports/numbers-fix-run1.json").exists()
