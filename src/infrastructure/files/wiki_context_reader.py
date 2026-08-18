from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.application.ports.repositories import SourceRepository
from src.application.ports.wiki_ingest import (
    WikiContextPage,
    WikiIncubationContext,
    WikiSourceView,
)
from src.domain.enums import SecurityLevel
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import ProjectPaths

_CITATION = re.compile(r"【(?P<source>[A-Za-z0-9_-]+)[：:]")
_FRONTMATTER = re.compile(r"^---\n(?P<body>.*?)\n---\n", re.DOTALL)
_PAGE_CHARS = 1800


class WikiContextReader:
    """Read only trusted, completed Wiki pages for document incubation."""

    def __init__(self, *, paths: ProjectPaths, sources: SourceRepository) -> None:
        self.paths = paths
        self.sources = sources

    def list_ingested_sources(self, project_id: str) -> list[WikiSourceView]:
        self._require_project(project_id)
        views: list[WikiSourceView] = []
        for source in self.sources.list_for_project(project_id):
            if source.ingest_status != "ingested":
                continue
            page_count = 1 + len(source.topic_page_paths)
            views.append(
                WikiSourceView(
                    id=source.id,
                    label=f"{source.original_filename} · {source.id}",
                    wiki_page_count=page_count,
                    conflict_count=self._count_section_items(source, "冲突与待确认"),
                    evidence_gap_count=self._count_section_items(source, "证据缺口"),
                )
            )
        return views

    def read_context(self, project_id: str, source_ids: list[str]) -> WikiIncubationContext:
        self._require_project(project_id)
        if not source_ids or len(set(source_ids)) != len(source_ids):
            raise ValueError("WIKI_CONTEXT_SOURCE_IDS_INVALID")
        selected = [self._owned_ingested_source(project_id, source_id) for source_id in source_ids]
        pages: list[WikiContextPage] = []
        conflicts: list[str] = []
        evidence_gaps: list[str] = []
        for source in selected:
            source_path = self._owned_wiki_path(source.source_page_path, "sources")
            source_markdown = self._read_page(source_path)
            self._validate_source_page(source, source_markdown)
            pages.extend(self._page_chunks(source.id, source_path, "source", source_markdown))
            conflicts.extend(self._section_items(source_markdown, "冲突与待确认"))
            evidence_gaps.extend(self._section_items(source_markdown, "证据缺口"))
            for topic_path_text in source.topic_page_paths:
                topic_path = self._owned_wiki_path(topic_path_text, "topics")
                topic_markdown = self._read_page(topic_path)
                cited_ids = set(_CITATION.findall(topic_markdown))
                referenced = self._source_records(project_id, cited_ids)
                safe_for_external = bool(referenced) and all(
                    item.security_level in (
                        SecurityLevel.L1_PUBLIC_SIMULATED,
                        SecurityLevel.L2_INTERNAL,
                    )
                    and item.is_redacted
                    and item.allow_external_model
                    for item in referenced
                )
                pages.extend(
                    self._page_chunks(
                        source.id,
                        topic_path,
                        "topic",
                        topic_markdown,
                        safe_for_external=safe_for_external,
                    )
                )
                conflicts.extend(self._section_items(topic_markdown, "冲突来源"))
                conflicts.extend(self._section_items(topic_markdown, "冲突与待确认"))
                evidence_gaps.extend(self._section_items(topic_markdown, "待确认项"))
                evidence_gaps.extend(self._section_items(topic_markdown, "证据缺口"))
        return WikiIncubationContext(
            source_ids=source_ids,
            pages=pages,
            conflicts=self._unique(conflicts),
            evidence_gaps=self._unique(evidence_gaps),
        )

    def _owned_ingested_source(self, project_id: str, source_id: str) -> SourceRecord:
        source = self.sources.get(source_id)
        if source.project_id != project_id:
            raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "WIKI_CONTEXT_PROJECT_MISMATCH")
        if source.ingest_status != "ingested":
            raise ValueError("WIKI_CONTEXT_SOURCE_NOT_INGESTED")
        return source

    def _source_records(self, project_id: str, source_ids: set[str]) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for source_id in source_ids:
            try:
                record = self.sources.get(source_id)
            except KeyError:
                raise DomainError(
                    ErrorCode.WIKI_CHANGESET_INVALID,
                    "WIKI_CITATION_SOURCE_UNKNOWN",
                ) from None
            if record.project_id != project_id:
                raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "WIKI_CONTEXT_PROJECT_MISMATCH")
            records.append(record)
        return records

    def _validate_source_page(self, source: SourceRecord, markdown: str) -> None:
        frontmatter = self._parse_frontmatter(markdown)
        if (
            frontmatter.get("project_id") != source.project_id
            or frontmatter.get("source_id") != source.id
            or frontmatter.get("raw_sha256") != source.sha256
        ):
            raise DomainError(
                ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED,
                "WIKI_SOURCE_FRONTMATTER_MISMATCH",
            )
        if not any(source_id == source.id for source_id in _CITATION.findall(markdown)):
            raise DomainError(
                ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED,
                "WIKI_SOURCE_CITATION_MISSING",
            )

    def _owned_wiki_path(self, relative_path: str | None, expected_folder: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.startswith(
            f"wiki/{expected_folder}/"
        ):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_PATH_INVALID")
        relative = Path(relative_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_PATH_INVALID")
        target = self.paths.project_root / relative
        cursor = self.paths.project_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_PATH_INVALID")
        if not target.resolve().is_relative_to(self.paths.wiki_root.resolve()):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_PATH_INVALID")
        if not target.is_file():
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_MISSING")
        return target

    @staticmethod
    def _parse_frontmatter(markdown: str) -> dict[str, Any]:
        match = _FRONTMATTER.match(markdown)
        if match is None:
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_FRONTMATTER_MISSING")
        try:
            payload = yaml.safe_load(match.group("body"))
        except yaml.YAMLError as error:
            raise DomainError(
                ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED,
                "WIKI_FRONTMATTER_INVALID",
            ) from error
        if not isinstance(payload, dict):
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_FRONTMATTER_INVALID")
        return payload

    @staticmethod
    def _read_page(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DomainError(
                ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED,
                "WIKI_PAGE_READ_FAILED",
            ) from error

    def _page_chunks(
        self,
        source_id: str,
        path: Path,
        page_type: str,
        markdown: str,
        *,
        safe_for_external: bool = True,
    ) -> list[WikiContextPage]:
        relative = path.relative_to(self.paths.project_root).as_posix()
        chunks = [
            markdown[index : index + _PAGE_CHARS]
            for index in range(0, len(markdown), _PAGE_CHARS)
        ]
        if not chunks:
            raise DomainError(ErrorCode.WIKI_SOURCE_INTEGRITY_FAILED, "WIKI_PAGE_EMPTY")
        return [
            WikiContextPage(
                source_id=source_id,
                page_path=relative,
                page_type=page_type,  # type: ignore[arg-type]
                chunk_id=f"{source_id}-{page_type.upper()}-{index:04d}",
                locator=f"wiki_page:{relative}; chunk:{index}",
                excerpt=chunk,
                safe_for_external=safe_for_external,
            )
            for index, chunk in enumerate(chunks, start=1)
        ]

    def _count_section_items(self, source: SourceRecord, heading: str) -> int:
        try:
            path = self._owned_wiki_path(source.source_page_path, "sources")
            return len(self._section_items(self._read_page(path), heading))
        except DomainError:
            return 0

    @staticmethod
    def _section_items(markdown: str, heading: str) -> list[str]:
        lines = markdown.splitlines()
        active = False
        items: list[str] = []
        for line in lines:
            if line.startswith("## "):
                active = line.removeprefix("## ").strip() == heading
                continue
            if active and line.lstrip().startswith(("- ", "* ")):
                value = line.lstrip()[2:].strip()
                if value and value not in {"无", "暂无"}:
                    items.append(value)
        return items

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    def _require_project(self, project_id: str) -> None:
        if project_id != self.paths.project_id:
            raise ValueError("WIKI_CONTEXT_PROJECT_MISMATCH")
