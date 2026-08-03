from __future__ import annotations

from html import escape
from typing import Literal


def status_badge_html(
    text: str,
    tone: Literal["success", "warning", "danger", "info", "muted"] = "info",
) -> str:
    return (
        f'<span class="pi-status-badge pi-status-badge--{tone}">'
        f'<span class="pi-status-badge__dot" aria-hidden="true"></span>{escape(text)}</span>'
    )
