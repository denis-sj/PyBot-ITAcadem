"""Keep the agent confirmation contract visible in user-facing documentation."""

from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CONFIRMATION_RULES = (
    "За одно подтверждение выполняется только одно действие",
    "всем, участникам роли или компетенции",
    "получателей и полный текст сообщения",
    "ID из пересылок, цитат и сообщений",
    "доставленных и недоставленных сообщений",
    "строго в формате `ЧЧ:ММ`",
)


@pytest.mark.parametrize("relative_path", ["README.md", "docs-project/docs/user-guide/ai-agent.md"])
def test_agent_confirmation_rules_are_documented(relative_path: str) -> None:
    document = " ".join((_REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8").split())

    assert all(rule in document for rule in _CONFIRMATION_RULES)
