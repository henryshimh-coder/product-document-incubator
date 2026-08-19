from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.wiki import WikiIngestStatus


class _WikiIngestCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)


class IngestArchivedSourceInput(_WikiIngestCommand):
    pass


class PrepareLocalWikiIngestInput(_WikiIngestCommand):
    pass


class ConfirmLocalWikiIngestInput(_WikiIngestCommand):
    pass


class RecoverWikiTransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)


class WikiIngestResultView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    status: WikiIngestStatus
    source_page_path: str | None
    topic_page_paths: list[str]
    conflict_count: int
    evidence_gap_count: int
    duplicate: bool = False


class LocalWikiIngestDraftView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    status: Literal[WikiIngestStatus.LOCAL_REVIEW_REQUIRED]
    draft_root: Path
