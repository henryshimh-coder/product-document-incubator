from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.application.dto.wiki_ingest import (
    LocalWikiIngestDraftView,
    PrepareLocalWikiIngestInput,
)
from src.application.ports.repositories import SourceRepository
from src.domain.enums import DocumentGenerationMode, SecurityLevel
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.domain.wiki import WikiIngestStatus
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.source_index_store import SourceIndexStore

WIKI_SCHEMA_VERSION = "2.2"
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class PrepareLocalWikiIngest:
    """Create an Owner-only L3/L4 Wiki draft without accessing a model Gateway."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        sources: SourceRepository,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.sources = sources
        self.now = now or (lambda: datetime.now(UTC))
        self.index = SourceIndexStore(paths)

    def execute(self, command: PrepareLocalWikiIngestInput) -> LocalWikiIngestDraftView:
        _resolve_project(self.paths, command.project_id)
        _validate_source_id(command.source_id)
        source = _owned_source(self.sources, command.project_id, command.source_id)
        _require_sensitive_source(source)
        _verified_raw(self.paths, source)
        draft_root = _draft_root(self.paths, source.id)

        if source.ingest_status == WikiIngestStatus.LOCAL_REVIEW_REQUIRED:
            _require_existing_draft(draft_root)
            return LocalWikiIngestDraftView(
                source_id=source.id,
                status=WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
                draft_root=draft_root,
            )
        if source.ingest_status not in {
            WikiIngestStatus.PENDING,
            WikiIngestStatus.FAILED,
            WikiIngestStatus.REINGEST_RECOMMENDED,
        }:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_STATUS_INVALID")
        if draft_root.exists():
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_ALREADY_EXISTS")

        try:
            draft_root.mkdir(parents=True, exist_ok=False)
            (draft_root / "topics").mkdir()
            (draft_root / "README.md").write_text(_readme(), encoding="utf-8")
            (draft_root / "source.md").write_text(
                _source_template(source, self.paths, self.now()), encoding="utf-8"
            )
            pending_owner_review = source.model_copy(
                update={
                    "ingest_status": WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
                    "ingest_error_code": None,
                    "generation_mode": DocumentGenerationMode.LOCAL_MANUAL,
                }
            )
            self.sources.update(pending_owner_review)
            self.index.upsert(pending_owner_review)
        except Exception:
            try:
                self.sources.update(source)
                self.index.upsert(source)
            except Exception:
                pass
            shutil.rmtree(draft_root, ignore_errors=True)
            raise
        return LocalWikiIngestDraftView(
            source_id=source.id,
            status=WikiIngestStatus.LOCAL_REVIEW_REQUIRED,
            draft_root=draft_root,
        )


def _resolve_project(paths: ProjectPaths, project_id: str) -> None:
    if project_id != paths.project_id:
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "PROJECT_ID_MISMATCH")
    root = paths.project_root
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "PROJECT_ROOT_INVALID")


def _validate_source_id(source_id: str) -> None:
    if _SOURCE_ID.fullmatch(source_id) is None:
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_ID_INVALID")


def _owned_source(
    sources: SourceRepository, project_id: str, source_id: str
) -> SourceRecord:
    try:
        source = sources.get(source_id)
    except KeyError:
        raise DomainError(ErrorCode.NOT_FOUND, "SOURCE_NOT_FOUND") from None
    if source.project_id != project_id:
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "SOURCE_PROJECT_MISMATCH")
    return source


def _require_sensitive_source(source: SourceRecord) -> None:
    if source.security_level not in {
        SecurityLevel.L3_CONFIDENTIAL,
        SecurityLevel.L4_RESTRICTED,
    }:
        raise DomainError(ErrorCode.WIKI_EXTERNAL_CALL_DENIED, "LOCAL_INGEST_L3_L4_ONLY")


def _verified_raw(paths: ProjectPaths, source: SourceRecord) -> Path:
    archive = Path(source.archive_path)
    if (
        "\\" in source.archive_path
        or archive.as_posix() != source.archive_path
        or any(part in {"", ".", ".."} for part in archive.parts)
    ):
        raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_PATH_INVALID")
    lexical = archive if archive.is_absolute() else paths.project_root / archive
    raw_root = paths.raw_root
    resolved = lexical.resolve()
    if (
        paths.project_root.is_symlink()
        or raw_root.is_symlink()
        or raw_root.resolve() != raw_root
        or lexical.is_symlink()
        or resolved != lexical
        or not resolved.is_relative_to(raw_root)
        or not resolved.is_file()
    ):
        raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_PATH_INVALID")
    try:
        payload = resolved.read_bytes()
    except OSError:
        raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_READ_FAILED") from None
    if len(payload) != source.size_bytes or hashlib.sha256(payload).hexdigest() != source.sha256:
        raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "RAW_SHA256_MISMATCH")
    return resolved


def _draft_root(paths: ProjectPaths, source_id: str) -> Path:
    root = paths.wiki_root / "drafts" / "local-ingest" / source_id
    resolved_parent = root.parent.resolve()
    draft_ancestors = (
        paths.wiki_root / "drafts",
        paths.wiki_root / "drafts" / "local-ingest",
    )
    if (
        paths.wiki_root.is_symlink()
        or any(
            ancestor.is_symlink()
            for ancestor in draft_ancestors
            if ancestor.exists()
        )
        or not resolved_parent.is_relative_to(paths.wiki_root.resolve())
        or root.is_symlink()
    ):
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_PATH_INVALID")
    return root


def _require_existing_draft(draft_root: Path) -> None:
    if (
        draft_root.is_symlink()
        or not draft_root.is_dir()
        or not (draft_root / "README.md").is_file()
        or not (draft_root / "source.md").is_file()
        or (draft_root / "topics").is_symlink()
        or not (draft_root / "topics").is_dir()
    ):
        raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, "LOCAL_DRAFT_MISSING")


def _relative_raw_path(paths: ProjectPaths, source: SourceRecord) -> str:
    archive = Path(source.archive_path)
    return (
        archive.relative_to(paths.project_root).as_posix()
        if archive.is_absolute()
        else archive.as_posix()
    )


def _source_template(source: SourceRecord, paths: ProjectPaths, created_at: datetime) -> str:
    frontmatter = {
        "project_id": source.project_id,
        "source_id": source.id,
        "material_series_id": source.material_series_id or source.id,
        "material_version": source.document_version,
        "raw_path": _relative_raw_path(paths, source),
        "raw_sha256": source.sha256,
        "source_type": source.source_type,
        "authority_level": source.authority_level.value,
        "security_level": source.security_level.value,
        "schema_version": WIKI_SCHEMA_VERSION,
        "generation_mode": DocumentGenerationMode.LOCAL_MANUAL.value,
        "ingested_at": created_at.isoformat(),
    }
    title = source.material_name or Path(source.original_filename).stem
    return (
        f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
        f"---\n# 来源：{title}\n\n## 来源摘要\n\n\n## 来源定位\n\n"
        f"- 归档来源【{source.id}：请填写本地定位】\n"
    )


def _readme() -> str:
    return """# 本地 Wiki Ingest 草稿

此草稿仅供 Owner 在本机 Markdown 或 Obsidian 中编辑，不会调用外部模型。

1. 在 `source.md` 的“来源摘要”和“来源定位”补全本地核验内容。
   不要修改其 Frontmatter 的项目、来源、Raw 路径或 SHA-256。
2. 在 `topics/` 新建或编辑主题 Markdown。每份主题必须有 Frontmatter、
   当前综合结论、支持来源、冲突来源和待确认项，并引用本来源 ID。
3. 回到“原始材料”选择“校验并确认本地 Ingest”。校验失败时，本目录会保留，修正后可再次确认。
"""
