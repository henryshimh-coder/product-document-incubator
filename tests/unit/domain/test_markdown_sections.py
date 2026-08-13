from __future__ import annotations

import pytest


def test_validate_product_markdown_requires_single_h1_and_rejects_placeholders() -> None:
    from src.infrastructure.files.markdown_sections import validate_product_markdown

    markdown = "# 产品方案\n\n## 概述\n\n正文"
    assert validate_product_markdown(markdown) == markdown
    with pytest.raises(ValueError, match="MARKDOWN_H1_INVALID"):
        validate_product_markdown("## 只有二级标题\n\n正文")
    with pytest.raises(ValueError, match="MARKDOWN_PLACEHOLDER_INVALID"):
        validate_product_markdown("# 产品方案\n\nTODO：补充内容")


def test_extract_headings_returns_plain_h1_h2_h3_only() -> None:
    from src.infrastructure.files.markdown_sections import extract_headings

    assert extract_headings("# 产品方案\n## 概述 **标题**\n### 细节 `A`\n#### 忽略") == [
        "产品方案",
        "概述 标题",
        "细节 A",
    ]
