from __future__ import annotations


def test_highlight_exact_escapes_html_and_marks_each_exact_keyword() -> None:
    """Catches sensitive comparison rendering source HTML or using unsafe substring markup."""
    from src.ui.components.sensitive_comparison import highlight_exact

    html = highlight_exact("<script>规则A与规则AB</script>", "规则A")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.count("<mark") == 2
