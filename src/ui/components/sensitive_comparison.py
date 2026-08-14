from __future__ import annotations

import html


def highlight_exact(text: str, keyword: str) -> str:
    escaped = html.escape(text)
    if not keyword:
        return escaped
    escaped_keyword = html.escape(keyword)
    return escaped.replace(escaped_keyword, f"<mark>{escaped_keyword}</mark>")
