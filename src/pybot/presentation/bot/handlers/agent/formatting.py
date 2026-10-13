"""Normalize model Markdown into readable plain Telegram text."""

import re

_CODE_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_TRAILING_HEADING_MARKS = re.compile(r"\s+#+\s*$")
_BULLET = re.compile(r"^(\s*)[-+*]\s+")
_QUOTE = re.compile(r"^\s{0,3}>\s?")
_HORIZONTAL_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD_ASTERISKS = re.compile(r"(?<!\w)\*\*(.+?)\*\*(?!\w)")
_BOLD_UNDERSCORES = re.compile(r"(?<!\w)__(.+?)__(?!\w)")
_ITALIC_ASTERISKS = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_ITALIC_UNDERSCORES = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")


def format_agent_text(text: str) -> str:
    """Remove common Markdown syntax while preserving headings and list structure."""
    formatted_lines: list[str] = []
    inside_code_block = False

    for source_line in text.splitlines():
        if _CODE_FENCE.match(source_line):
            inside_code_block = not inside_code_block
            continue

        if inside_code_block:
            line = f"    {source_line.rstrip()}" if source_line.strip() else ""
        elif _HORIZONTAL_RULE.fullmatch(source_line):
            line = ""
        else:
            line_text = source_line
            heading = _HEADING.match(source_line)
            if heading is not None:
                line_text = _TRAILING_HEADING_MARKS.sub("", heading.group(1))
                if formatted_lines and formatted_lines[-1]:
                    formatted_lines.append("")
            else:
                line_text = _QUOTE.sub("│ ", line_text)
                line_text = _BULLET.sub(r"\1• ", line_text, count=1)
            line = _format_inline_markdown(line_text).rstrip()

        if line or (formatted_lines and formatted_lines[-1]):
            formatted_lines.append(line)

    while formatted_lines and not formatted_lines[0]:
        formatted_lines.pop(0)
    while formatted_lines and not formatted_lines[-1]:
        formatted_lines.pop()

    return "\n".join(formatted_lines)


def _format_inline_markdown(text: str) -> str:
    text = _IMAGE.sub(r"\1", text)
    text = _MARKDOWN_LINK.sub(_replace_markdown_link, text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = text.replace("`", "")
    text = _BOLD_ASTERISKS.sub(r"\1", text)
    text = _BOLD_UNDERSCORES.sub(r"\1", text)
    text = _ITALIC_ASTERISKS.sub(r"\1", text)
    return _ITALIC_UNDERSCORES.sub(r"\1", text)


def _replace_markdown_link(match: re.Match[str]) -> str:
    label, destination = match.groups()
    if destination.startswith("/"):
        return destination
    if destination.startswith(("https://", "http://")):
        return f"{label} ({destination})"
    return label
