from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.domain.enums import SecurityLevel


class LintDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RunLintInput(LintDto):
    project_id: str
    scope: Literal["current", "current_plus_source", "all_current_sources"]
    source_id: str | None = None
    preferred_mode: Literal["realtime", "cache"] = "realtime"


class ListLintIssuesInput(LintDto):
    project_id: str
    view: Literal[
        "all_open",
        "blocking",
        "pending_decision",
        "pending_info",
        "processed",
        "false_positive",
    ] = "all_open"
    sort_by: Literal["severity", "updated"] = "severity"


class LintComparisonPackage(LintDto):
    inputs: dict[str, Any]
    source_total_chars: int
    security_level: SecurityLevel
    # 缓存身份的来源 SHA-256：current_plus_source 为对比来源，其余为参与材料
    # 内容哈希的确定性合成；必须由真实运行时输入重建。
    cache_source_sha256: str = ""
