"""
Converts standard markdown from the Sumo agent response into Slack mrkdwn.

Slack mrkdwn differs from standard markdown:
- Bold: *text* not **text**
- Italic: _text_ not *text*
- No heading support (# is literal)
- Links: <url|text> not [text](url)
- Code blocks: ``` with no language specifier
"""

import re


def markdown_to_slack_mrkdwn(text: str) -> str:
    if not text:
        return text

    # Protect code blocks from transformation
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

    result = re.sub(r"```[\s\S]*?```", save_code_block, text)

    # Convert markdown links [text](url) → <url|text>
    result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", result)

    # Convert ### headings → *bold* (Slack doesn't render #)
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

    # Convert **bold** → *bold* (Slack uses single asterisk)
    result = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", result)

    # Remove language specifiers from code blocks: ```python → ```
    for i, block in enumerate(code_blocks):
        cleaned = re.sub(r"```\w+\n", "```\n", block)
        code_blocks[i] = cleaned

    # Convert HTML entities that might come from Sumo response
    result = result.replace("&amp;", "&")
    result = result.replace("&lt;", "<")
    result = result.replace("&gt;", ">")

    # Convert --- horizontal rules → ———
    result = re.sub(r"^-{3,}$", "———", result, flags=re.MULTILINE)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        result = result.replace(f"__CODE_BLOCK_{i}__", block)

    return result


def truncate_for_slack(text: str, max_length: int = 3000) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 20] + "\n\n_(truncated)_"
