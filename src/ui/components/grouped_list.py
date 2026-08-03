from __future__ import annotations

from dataclasses import dataclass
from html import escape

import streamlit as st


@dataclass(frozen=True)
class GroupedListItem:
    icon: str
    title: str
    detail: str
    trailing: str
    href: str | None = None


def render_grouped_list(
    *,
    title: str,
    items: list[GroupedListItem],
    test_id: str,
    variant: str = "default",
) -> None:
    rows = "".join(_row_html(item) for item in items)
    st.markdown(
        f'<section class="pi-list-section" data-testid="{escape(test_id, quote=True)}">'
        f'<h2 class="pi-section-title">{escape(title)}</h2>'
        f'<div class="pi-grouped-list pi-grouped-list--{escape(variant, quote=True)}">'
        f"{rows}</div></section>",
        unsafe_allow_html=True,
    )


def _row_html(item: GroupedListItem) -> str:
    tag = "a" if item.href is not None else "div"
    href = "" if item.href is None else f' href="{escape(item.href, quote=True)}"'
    return (
        f'<{tag} class="pi-grouped-list__row"{href}>'
        f'<span class="pi-grouped-list__icon" aria-hidden="true">{escape(item.icon)}</span>'
        '<span class="pi-grouped-list__content">'
        f'<span class="pi-grouped-list__title">{escape(item.title)}</span>'
        f'<span class="pi-grouped-list__detail">{escape(item.detail)}</span>'
        "</span>"
        f'<span class="pi-grouped-list__trailing">{escape(item.trailing)}</span>'
        f"</{tag}>"
    )
