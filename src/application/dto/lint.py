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


class LintComparisonPackage(LintDto):
    inputs: dict[str, Any]
    source_total_chars: int
    security_level: SecurityLevel
