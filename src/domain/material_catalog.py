from __future__ import annotations

from dataclasses import dataclass

from src.domain.enums import AuthorityLevel


@dataclass(frozen=True)
class MaterialTypeDefinition:
    code: str
    label: str
    description: str
    examples: tuple[str, ...]
    order: int


MATERIAL_TYPES: tuple[MaterialTypeDefinition, ...] = (
    MaterialTypeDefinition(
        code="product_requirement",
        label="产品需求",
        description="产品目标、用户场景、功能、范围、交互规则和验收要求",
        examples=("PRD", "需求说明", "产品方案", "功能清单"),
        order=1,
    ),
    MaterialTypeDefinition(
        code="business_rule",
        label="业务规则",
        description="业务准入、流程、计算口径、状态流转和操作规则",
        examples=("审批规则", "额度规则", "业务流程", "规则说明"),
        order=2,
    ),
    MaterialTypeDefinition(
        code="customer_market_material",
        label="用户与市场研究",
        description="用于验证用户需求、市场判断和竞品结论的证据",
        examples=("用户访谈", "问卷", "市场报告", "竞品分析", "需求验证"),
        order=3,
    ),
    MaterialTypeDefinition(
        code="meeting_minutes",
        label="会议与决策",
        description="会议讨论、评审结论、决策和待办事项",
        examples=("会议纪要", "评审记录", "决策记录"),
        order=4,
    ),
    MaterialTypeDefinition(
        code="risk_compliance",
        label="风险与合规",
        description="风控、法务、合规、隐私和信息安全要求",
        examples=("风险意见", "合规审查", "法务意见", "隐私要求"),
        order=5,
    ),
    MaterialTypeDefinition(
        code="technical_specification",
        label="技术与接口",
        description="系统架构、接口、数据结构、技术限制和非功能要求",
        examples=("技术方案", "API 文档", "数据字典", "系统约束"),
        order=6,
    ),
    MaterialTypeDefinition(
        code="operation_feedback",
        label="运营与反馈",
        description="上线运营、客服反馈、用户投诉、复盘和效果观察",
        examples=("运营方案", "客服记录", "用户反馈", "上线复盘"),
        order=7,
    ),
    MaterialTypeDefinition(
        code="other",
        label="其他参考材料",
        description="暂不属于上述分类、但可供产品文档孵化参考的材料",
        examples=("补充说明", "外部参考资料"),
        order=8,
    ),
)

MATERIAL_TYPES_BY_CODE = {item.code: item for item in MATERIAL_TYPES}
NEW_AUTHORITY_LEVELS = (
    AuthorityLevel.FORMAL_EFFECTIVE,
    AuthorityLevel.DISCUSSION_REFERENCE,
)


def require_new_material_type(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or value not in MATERIAL_TYPES_BY_CODE:
        raise ValueError("MATERIAL_TYPE_INVALID")
    return value


def material_type_label(value: str) -> str:
    definition = MATERIAL_TYPES_BY_CODE.get(value)
    return definition.label if definition else f"历史类型：{value}"


def authority_label(value: AuthorityLevel) -> str:
    labels = {
        AuthorityLevel.FORMAL_EFFECTIVE: "正式基线依据",
        AuthorityLevel.FORMAL_DECISION: "正式基线依据（历史值）",
        AuthorityLevel.PROFESSIONAL_OPINION: "参考材料（历史值）",
        AuthorityLevel.DISCUSSION_REFERENCE: "参考材料",
    }
    return labels[value]
