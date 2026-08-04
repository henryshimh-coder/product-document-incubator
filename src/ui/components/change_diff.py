from __future__ import annotations

import difflib
from html import escape

import streamlit as st

_PANEL_STYLE = (
    "border:1px solid #E4E7EC;border-radius:8px;padding:12px 14px;"
    "font-size:14px;line-height:1.7;word-break:break-word;background:{bg};"
)


def render_change_diff(
    *,
    before: str,
    after: str,
    before_label: str = "修改前",
    after_label: str = "修改后",
) -> None:
    """Render the authoritative before/after content side by side."""
    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown(f"**{escape(before_label)}**")
        _panel(before, "#F8FAFC")
    with right:
        st.markdown(f"**{escape(after_label)}**")
        _panel(after, "#F0F7FF")


def render_diff_summary(*, before: str, after: str) -> None:
    """Render a line-level diff summary for audit and result views."""
    rows: list[str] = []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("? "):
            continue
        if line.startswith("- "):
            rows.append(
                '<div style="background:#FDECEC;color:#B42318;border-radius:4px;'
                f'padding:0 6px;">- {escape(line[2:])}</div>'
            )
        elif line.startswith("+ "):
            rows.append(
                '<div style="background:#E7F6EC;color:#067647;border-radius:4px;'
                f'padding:0 6px;">+ {escape(line[2:])}</div>'
            )
        else:
            rows.append(f'<div style="color:#475467;padding:0 6px;">{escape(line[2:])}</div>')
    st.markdown(
        f'<div style="{_PANEL_STYLE.format(bg="#FFFFFF")}">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def _panel(text: str, background: str) -> None:
    st.markdown(
        f'<div style="{_PANEL_STYLE.format(bg=background)}">'
        f"{escape(text).replace(chr(10), '<br>')}</div>",
        unsafe_allow_html=True,
    )
