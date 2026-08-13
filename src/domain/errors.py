from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    FILE_TYPE_NOT_ALLOWED = "FILE_TYPE_NOT_ALLOWED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DUPLICATE_SOURCE = "DUPLICATE_SOURCE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    REDACTION_REQUIRED = "REDACTION_REQUIRED"
    EXTERNAL_CALL_DENIED = "EXTERNAL_CALL_DENIED"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    CACHE_NOT_FOUND = "CACHE_NOT_FOUND"
    CITATION_INVALID = "CITATION_INVALID"
    BASELINE_INTEGRITY_FAILED = "BASELINE_INTEGRITY_FAILED"
    DECISION_INVALID = "DECISION_INVALID"
    CHANGE_NOT_APPROVED = "CHANGE_NOT_APPROVED"
    RELEASE_LOCKED = "RELEASE_LOCKED"
    RELEASE_FAILED = "RELEASE_FAILED"
    INVALID_CHANGE_TRANSITION = "INVALID_CHANGE_TRANSITION"
    SANDBOX_SOURCE_NOT_ALLOWED = "SANDBOX_SOURCE_NOT_ALLOWED"
    SOURCE_AUTHORITY_NOT_FORMAL = "SOURCE_AUTHORITY_NOT_FORMAL"
    CHANGE_REVIEW_INVALID = "CHANGE_REVIEW_INVALID"
    RELEASE_PROJECT_MISMATCH = "RELEASE_PROJECT_MISMATCH"
    RELEASE_CHANGE_MISMATCH = "RELEASE_CHANGE_MISMATCH"
    IMPACT_REVIEW_REQUIRED = "IMPACT_REVIEW_REQUIRED"
    INVALID_RELEASE_NOTE = "INVALID_RELEASE_NOTE"
    RELEASE_APPROVER_REQUIRED = "RELEASE_APPROVER_REQUIRED"
    TARGET_VERSION_ALREADY_EFFECTIVE = "TARGET_VERSION_ALREADY_EFFECTIVE"
    TARGET_VERSION_ALREADY_EXISTS = "TARGET_VERSION_ALREADY_EXISTS"
    SOURCE_METADATA_MISMATCH = "SOURCE_METADATA_MISMATCH"
    OUTBOUND_COVERAGE_EXCEEDED = "OUTBOUND_COVERAGE_EXCEEDED"
    INGEST_PERSISTENCE_FAILED = "INGEST_PERSISTENCE_FAILED"
    HISTORICAL_VERSION_REQUIRED = "HISTORICAL_VERSION_REQUIRED"
    HISTORICAL_VERSION_INVALID = "HISTORICAL_VERSION_INVALID"
    DECISION_FIELDS_REQUIRED = "DECISION_FIELDS_REQUIRED"
    CHANGE_FIELDS_REQUIRED = "CHANGE_FIELDS_REQUIRED"
    DECISION_IDEMPOTENCY_CONFLICT = "DECISION_IDEMPOTENCY_CONFLICT"
    DECISION_PERSISTENCE_FAILED = "DECISION_PERSISTENCE_FAILED"
    LINT_SOURCE_REQUIRED = "LINT_SOURCE_REQUIRED"
    LINT_SOURCE_NOT_COMPARABLE = "LINT_SOURCE_NOT_COMPARABLE"
    LINT_DETERMINISTIC_LIMIT_EXCEEDED = "LINT_DETERMINISTIC_LIMIT_EXCEEDED"
    CHANGE_NOT_REVIEWABLE = "CHANGE_NOT_REVIEWABLE"
    REVIEW_IDEMPOTENCY_CONFLICT = "REVIEW_IDEMPOTENCY_CONFLICT"
    REVIEW_PERSISTENCE_FAILED = "REVIEW_PERSISTENCE_FAILED"
    RELEASE_BLOCKED = "RELEASE_BLOCKED"
    RELEASE_MIRROR_REPAIR_REQUIRED = "RELEASE_MIRROR_REPAIR_REQUIRED"
    COST_SOURCE_REQUIRED = "COST_SOURCE_REQUIRED"
    COST_INPUT_INCOMPLETE = "COST_INPUT_INCOMPLETE"
    LINT_PERSISTENCE_FAILED = "LINT_PERSISTENCE_FAILED"
    PUBLISH_SOURCE_INTEGRITY_FAILED = "PUBLISH_SOURCE_INTEGRITY_FAILED"
    PUBLISH_CITATION_UNVERIFIABLE = "PUBLISH_CITATION_UNVERIFIABLE"
    RELATION_CONFLICT = "RELATION_CONFLICT"
    BASELINE_NOT_FOUND = "BASELINE_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ErrorDefinition:
    user_message: str
    retryable: bool = False


ERROR_CATALOG: dict[ErrorCode, ErrorDefinition] = {
    ErrorCode.FILE_TYPE_NOT_ALLOWED: ErrorDefinition("不支持该文件格式"),
    ErrorCode.FILE_TOO_LARGE: ErrorDefinition("文件超过 20MB"),
    ErrorCode.DUPLICATE_SOURCE: ErrorDefinition("该文件已导入"),
    ErrorCode.EXTRACTION_FAILED: ErrorDefinition("无法读取文件内容", retryable=True),
    ErrorCode.REDACTION_REQUIRED: ErrorDefinition("资料尚未完成脱敏确认"),
    ErrorCode.EXTERNAL_CALL_DENIED: ErrorDefinition("该资料不满足外部模型调用条件"),
    ErrorCode.MODEL_TIMEOUT: ErrorDefinition("实时模型响应超时", retryable=True),
    ErrorCode.MODEL_OUTPUT_INVALID: ErrorDefinition("模型结果未通过结构校验", retryable=True),
    ErrorCode.CACHE_NOT_FOUND: ErrorDefinition("没有与当前材料完全匹配的缓存"),
    ErrorCode.CITATION_INVALID: ErrorDefinition("结论引用无法验证"),
    ErrorCode.BASELINE_INTEGRITY_FAILED: ErrorDefinition("当前基线完整性校验失败"),
    ErrorCode.DECISION_INVALID: ErrorDefinition("会议决定信息不完整"),
    ErrorCode.CHANGE_NOT_APPROVED: ErrorDefinition("变更尚未批准"),
    ErrorCode.RELEASE_LOCKED: ErrorDefinition("另一个发布操作正在执行", retryable=True),
    ErrorCode.RELEASE_FAILED: ErrorDefinition(
        "发布未完成，原产品版本仍然生效",
        retryable=True,
    ),
    ErrorCode.INVALID_CHANGE_TRANSITION: ErrorDefinition("当前状态不允许执行此操作"),
    ErrorCode.SANDBOX_SOURCE_NOT_ALLOWED: ErrorDefinition("模拟材料不能进入正式基线"),
    ErrorCode.SOURCE_AUTHORITY_NOT_FORMAL: ErrorDefinition("资料权威级别不满足正式基线要求"),
    ErrorCode.CHANGE_REVIEW_INVALID: ErrorDefinition("变更审批记录不完整"),
    ErrorCode.RELEASE_PROJECT_MISMATCH: ErrorDefinition("发布对象不属于同一项目"),
    ErrorCode.RELEASE_CHANGE_MISMATCH: ErrorDefinition("发布命令与待发布变更不一致"),
    ErrorCode.IMPACT_REVIEW_REQUIRED: ErrorDefinition("发布前必须完成影响检查"),
    ErrorCode.INVALID_RELEASE_NOTE: ErrorDefinition("发布说明须为 20–200 个字符"),
    ErrorCode.RELEASE_APPROVER_REQUIRED: ErrorDefinition("发布审批人不能为空"),
    ErrorCode.TARGET_VERSION_ALREADY_EFFECTIVE: ErrorDefinition("目标版本已是当前生效版本"),
    ErrorCode.TARGET_VERSION_ALREADY_EXISTS: ErrorDefinition("目标版本已存在"),
    ErrorCode.SOURCE_METADATA_MISMATCH: ErrorDefinition("同一材料的属性或安全设置与原记录不一致"),
    ErrorCode.OUTBOUND_COVERAGE_EXCEEDED: ErrorDefinition("材料过短，无法满足最小外调覆盖率预算"),
    ErrorCode.INGEST_PERSISTENCE_FAILED: ErrorDefinition(
        "导入结果未能安全写入，当前基线不受影响",
        retryable=True,
    ),
    ErrorCode.HISTORICAL_VERSION_REQUIRED: ErrorDefinition("历史查询必须指定产品版本"),
    ErrorCode.HISTORICAL_VERSION_INVALID: ErrorDefinition("指定的版本不是可查询的历史基线"),
    ErrorCode.DECISION_FIELDS_REQUIRED: ErrorDefinition("会议决定缺少当前操作的必填信息"),
    ErrorCode.CHANGE_FIELDS_REQUIRED: ErrorDefinition("变更单缺少修改前后、依据或目标版本"),
    ErrorCode.DECISION_IDEMPOTENCY_CONFLICT: ErrorDefinition("重复提交键对应的会议结论不一致"),
    ErrorCode.DECISION_PERSISTENCE_FAILED: ErrorDefinition(
        "会议决定未能安全写入，原问题状态不受影响",
        retryable=True,
    ),
    ErrorCode.LINT_SOURCE_REQUIRED: ErrorDefinition("当前自检范围需要指定比较资料"),
    ErrorCode.LINT_SOURCE_NOT_COMPARABLE: ErrorDefinition("指定资料没有可比较的候选或冲突卡片"),
    ErrorCode.LINT_DETERMINISTIC_LIMIT_EXCEEDED: ErrorDefinition(
        "本地确定性问题超过单次安全自检上限"
    ),
    ErrorCode.CHANGE_NOT_REVIEWABLE: ErrorDefinition("变更当前状态不允许复核"),
    ErrorCode.REVIEW_IDEMPOTENCY_CONFLICT: ErrorDefinition("重复提交键对应的复核内容不一致"),
    ErrorCode.REVIEW_PERSISTENCE_FAILED: ErrorDefinition(
        "复核结果未能安全写入，变更状态不受影响",
        retryable=True,
    ),
    ErrorCode.RELEASE_BLOCKED: ErrorDefinition(
        "发布已暂停：本地镜像与权威基线不一致，请先完成恢复",
        retryable=True,
    ),
    ErrorCode.RELEASE_MIRROR_REPAIR_REQUIRED: ErrorDefinition(
        "新版本已生效，但本地镜像修复失败，请重新校验后再试"
    ),
    ErrorCode.COST_SOURCE_REQUIRED: ErrorDefinition("成本参数缺少来源"),
    ErrorCode.COST_INPUT_INCOMPLETE: ErrorDefinition("成本参数不完整，无法计算"),
    ErrorCode.LINT_PERSISTENCE_FAILED: ErrorDefinition("自检结果写入失败，请重试", retryable=True),
    ErrorCode.PUBLISH_SOURCE_INTEGRITY_FAILED: ErrorDefinition(
        "正式来源材料未通过发布前完整性校验"
    ),
    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE: ErrorDefinition(
        "正式依据缺少可验证的引用定位，无法发布"
    ),
    ErrorCode.RELATION_CONFLICT: ErrorDefinition("追溯关系数据冲突，已阻断写入"),
    ErrorCode.BASELINE_NOT_FOUND: ErrorDefinition("当前没有已生效产品方案"),
    ErrorCode.NOT_FOUND: ErrorDefinition("未找到目标记录"),
}


class AppError(Exception):
    def __init__(self, code: ErrorCode | str, detail: str | None = None):
        normalized_code = ErrorCode(code)
        definition = ERROR_CATALOG[normalized_code]
        self.code = normalized_code.value
        self.user_message = definition.user_message
        self.retryable = definition.retryable
        self.detail = detail
        message = self.code if detail is None else f"{self.code}: {detail}"
        super().__init__(message)


class DomainError(AppError):
    pass


class GatewayError(AppError):
    """Safe external-workflow failure using the public application error catalog."""

    @classmethod
    def authorization_failed(cls) -> GatewayError:
        return cls(ErrorCode.EXTERNAL_CALL_DENIED, "DIFY_AUTH_FAILED")

    @classmethod
    def request_invalid(cls) -> GatewayError:
        return cls(ErrorCode.EXTERNAL_CALL_DENIED, "DIFY_REQUEST_INVALID")

    @classmethod
    def input_rejected(cls) -> GatewayError:
        return cls(ErrorCode.EXTERNAL_CALL_DENIED, "DIFY_INPUT_REJECTED")

    @classmethod
    def workflow_input_invalid(cls, detail: str) -> GatewayError:
        return cls(ErrorCode.EXTERNAL_CALL_DENIED, detail)

    @classmethod
    def sensitive_input_detected(cls) -> GatewayError:
        return cls(ErrorCode.REDACTION_REQUIRED, "SENSITIVE_INPUT_DETECTED")

    @classmethod
    def outbound_safety_proof_invalid(cls) -> GatewayError:
        return cls(ErrorCode.REDACTION_REQUIRED, "OUTBOUND_SAFETY_PROOF_INVALID")

    @classmethod
    def timeout(cls) -> GatewayError:
        return cls(ErrorCode.MODEL_TIMEOUT, "DIFY_TIMEOUT")

    @classmethod
    def temporarily_unavailable(cls) -> GatewayError:
        return cls(ErrorCode.MODEL_TIMEOUT, "DIFY_TEMPORARILY_UNAVAILABLE")

    @classmethod
    def transport_failed(cls) -> GatewayError:
        return cls(ErrorCode.EXTERNAL_CALL_DENIED, "DIFY_TRANSPORT_FAILED")


class OutputValidationError(AppError):
    """A model response that failed the trusted local output contract."""

    def __init__(self, detail: str = "MODEL_OUTPUT_INVALID") -> None:
        super().__init__(ErrorCode.MODEL_OUTPUT_INVALID, detail)
