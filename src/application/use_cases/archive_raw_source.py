from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dto.documents import ArchivedSourceView, ArchiveRawSourceInput
from src.application.ports.repositories import SourceRepository
from src.domain.models import SourceRecord
from src.domain.services.file_safety import detect_mime_type
from src.infrastructure.files.project_library import ProjectPaths


class ArchiveRawSource:
    def __init__(
        self,
        *,
        paths: ProjectPaths,
        sources: SourceRepository,
        archive_factory,
        index,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.sources = sources
        self.archive_factory = archive_factory
        self.index = index
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView:
        if command.project_id != self.paths.project_id:
            raise ValueError("archive command project_id does not match active project")
        local_path = command.local_path.expanduser().resolve()
        payload = local_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        existing = self.sources.find_by_sha256(command.project_id, digest)
        if existing is not None:
            return self._view(existing, duplicate=True)
        source_id = f"SRC-{command.project_id}-{digest[:16].upper()}"
        archived = self.archive_factory(source_id, command.document_date.year).copy_from(local_path)
        mime_type = detect_mime_type(payload)
        if mime_type is None:
            raise ValueError("archive MIME type is invalid")
        source = SourceRecord(
            id=source_id,
            project_id=command.project_id,
            original_filename=local_path.name,
            archive_path=str(archived.path),
            sha256=archived.sha256,
            mime_type=mime_type,
            size_bytes=archived.size_bytes,
            source_type=command.source_type,
            authority_level=command.authority_level,
            source_department=command.source_department,
            provider=None,
            document_date=command.document_date,
            document_version=command.document_version,
            applicable_baseline_version="未关联基线",
            security_level=command.security_level,
            is_redacted=command.is_redacted_confirmed,
            allow_external_model=command.allow_external_model,
            is_sandbox=False,
            ingest_status="archived",
            created_at=self.now(),
        )
        self.sources.add(source)
        try:
            self.index.upsert(source)
        except (OSError, ValueError):
            self.sources.update_ingest_status(source.id, "index_failed")
            raise RuntimeError("SOURCE_INDEX_WRITE_FAILED") from None
        return self._view(source, duplicate=archived.duplicate)

    @staticmethod
    def _view(source: SourceRecord, *, duplicate: bool) -> ArchivedSourceView:
        return ArchivedSourceView(
            source_id=source.id,
            project_id=source.project_id,
            filename=source.original_filename,
            archive_path=source.archive_path,
            sha256=source.sha256,
            size_bytes=source.size_bytes,
            source_type=source.source_type,
            ingest_status=source.ingest_status,
            duplicate=duplicate,
            created_at=source.created_at,
        )
