from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field

from src.domain.enums import DocumentGenerationMode
from src.domain.models import DomainModel, NonEmptyStr, Sha256Str

_INDEX_PATH = "wiki/index.md"
_LOG_PATH = "wiki/log.md"
_SOURCE_INDEX_PATH = ".incubator/source-index.json"


class WikiIngestStatus(StrEnum):
    PENDING = "pending_ingest"
    PROCESSING = "ingesting"
    INGESTED = "ingested"
    FAILED = "ingest_failed"
    REINGEST_RECOMMENDED = "reingest_recommended"
    LOCAL_REVIEW_REQUIRED = "local_review_required"


class WikiPageChange(DomainModel):
    relative_path: NonEmptyStr
    operation: Literal["create", "replace"]
    before_sha256: Sha256Str | None
    markdown: NonEmptyStr
    after_sha256: Sha256Str


class WikiChangeSet(DomainModel):
    transaction_id: NonEmptyStr
    project_id: NonEmptyStr
    source_id: NonEmptyStr
    idempotency_key: Sha256Str
    schema_version: Literal["2.2"]
    generation_mode: DocumentGenerationMode
    page_changes: list[WikiPageChange]
    source_page_path: NonEmptyStr
    topic_page_paths: list[NonEmptyStr]
    conflict_count: int = Field(ge=0)
    evidence_gap_count: int = Field(ge=0)
    result_digest: Sha256Str
    # Immutable Raw evidence captured before a transaction starts.  Defaults
    # preserve construction compatibility for old pure-domain callers; the
    # coordinator rejects those sentinel values before any write.
    raw_path: NonEmptyStr = "raw/unknown"
    raw_sha256: Sha256Str = "0" * 64
    raw_size_bytes: int = Field(default=0, ge=0)

    def validate_contract(self) -> None:
        paths = [change.relative_path for change in self.page_changes]
        if not paths:
            raise ValueError("WIKI_CHANGESET_PAGE_CHANGES_REQUIRED")
        if len(paths) != len(set(paths)):
            raise ValueError("WIKI_CHANGESET_TARGET_DUPLICATE")

        for change in self.page_changes:
            self._validate_target(change.relative_path)
            if change.operation == "create" and change.before_sha256 is not None:
                raise ValueError("WIKI_CHANGESET_CREATE_BEFORE_SHA_FORBIDDEN")
            if change.operation == "replace" and change.before_sha256 is None:
                raise ValueError("WIKI_CHANGESET_BEFORE_SHA_REQUIRED")

        source_paths = [path for path in paths if path.startswith("wiki/sources/")]
        if len(source_paths) != 1:
            raise ValueError("WIKI_CHANGESET_SOURCE_PAGE_REQUIRED")
        if self.source_page_path != source_paths[0]:
            raise ValueError("WIKI_CHANGESET_SOURCE_PAGE_MISMATCH")

        changed_topic_paths = [path for path in paths if path.startswith("wiki/topics/")]
        if len(self.topic_page_paths) != len(set(self.topic_page_paths)):
            raise ValueError("WIKI_CHANGESET_TOPIC_DUPLICATE")
        if set(self.topic_page_paths) != set(changed_topic_paths):
            raise ValueError("WIKI_CHANGESET_TOPIC_PAGE_MISMATCH")

        required_paths = {_INDEX_PATH, _LOG_PATH, _SOURCE_INDEX_PATH}
        if not required_paths.issubset(paths):
            raise ValueError("WIKI_CHANGESET_INDEX_LOG_SOURCE_INDEX_REQUIRED")

    @staticmethod
    def _validate_target(relative_path: str) -> None:
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or "\\" in relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
            or str(path) != relative_path
        ):
            raise ValueError("WIKI_CHANGESET_TARGET_FORBIDDEN")
        if relative_path in {_INDEX_PATH, _LOG_PATH, _SOURCE_INDEX_PATH}:
            return
        if (
            relative_path.startswith("wiki/sources/")
            or relative_path.startswith("wiki/topics/")
        ) and relative_path.endswith(".md"):
            return
        raise ValueError("WIKI_CHANGESET_TARGET_FORBIDDEN")


class WikiIngestRun(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    source_id: NonEmptyStr
    transaction_id: NonEmptyStr
    idempotency_key: Sha256Str
    schema_version: Literal["2.2"]
    generation_mode: DocumentGenerationMode
    status: NonEmptyStr
    source_page_path: NonEmptyStr | None = None
    topic_page_paths: list[NonEmptyStr] = Field(default_factory=list)
    result_digest: Sha256Str | None = None
    error_code: NonEmptyStr | None = None
    started_at: datetime
    finished_at: datetime | None = None


class WikiTransactionResult(DomainModel):
    transaction_id: NonEmptyStr
    idempotency_key: Sha256Str
    status: Literal["committed", "rolled_back", "recovery_required"]
