from __future__ import annotations

from html import escape

from src.domain.models import Project


def project_context_html(project: Project) -> str:
    fields = (
        ("产品线", project.product_line),
        ("项目阶段", project.stage),
        ("演示操作员", "产品经理"),
        ("创建日期", project.created_at.date().isoformat()),
        ("项目状态", "进行中"),
    )
    return "".join(
        '<span class="pi-project-meta__item">'
        f'<span class="pi-project-meta__label">{escape(label)}</span>'
        f"{escape(value)}"
        "</span>"
        for label, value in fields
    )
