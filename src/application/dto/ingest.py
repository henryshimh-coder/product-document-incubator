from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import AuthorityLevel, SecurityLevel


class ImportSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    uploaded_name: str = Field(min_length=1)
    uploaded_bytes: bytes = Field(min_length=1)
    source_type: str = Field(min_length=1)
    authority_level: AuthorityLevel
    source_department: str = Field(min_length=1)
    provider: str | None
    document_date: date
    document_version: str = Field(min_length=1)
    applicable_baseline_version: str = Field(min_length=1)
    security_level: SecurityLevel
    is_redacted_confirmed: bool
    allow_external_model: bool
    is_sandbox: bool
    preferred_mode: Literal["realtime", "cache"] = "realtime"
