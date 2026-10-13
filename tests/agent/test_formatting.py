from pybot.presentation.bot.handlers.agent.formatting import format_agent_text


def test_format_agent_text_converts_common_markdown_to_clean_telegram_text():
    markdown = """Привет, **ITAcadem**!

### 📋 Для всех пользователей
- **Мой профиль и баллы** — `/profile` (уровни, баллы)
- [Справка](https://example.org/help)
"""

    assert (
        format_agent_text(markdown)
        == """Привет, ITAcadem!

📋 Для всех пользователей
• Мой профиль и баллы — /profile (уровни, баллы)
• Справка (https://example.org/help)"""
    )


def test_format_agent_text_removes_fences_and_keeps_code_readable():
    markdown = """Используйте:
```python
print("Привет")
```"""

    assert format_agent_text(markdown) == 'Используйте:\n    print("Привет")'
