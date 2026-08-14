from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dto.materials import ArchivedSourceView, ArchiveRawSourceInput
from src.application.ports.repositories import ProjectRepository, SourceRepository
from src.domain.enums import SecurityLevel
from src.domain.models import SourceRecord
from src.domain.services.file_safety import detect_mime_type
from src.infrastructure.files.project_library import ProjectPaths


class ArchiveRawSource:
    def __init__(
        self,
        *,
        paths: ProjectPaths,
        projects: ProjectRepository | None = None,
        sources: SourceRepository,
        archive_factory,
        index,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.projects = projects
        self.sources = sources
        self.archive_factory = archive_factory
        self.index = index
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView:
        if command.project_id != self.paths.project_id:
            raise ValueError("archive command project_id does not match active project")
        if command.allow_external_model:
            if (
                command.security_level
                in (
                    SecurityLevel.L3_CONFIDENTIAL,
                    SecurityLevel.L4_RESTRICTED,
                )
                or not command.is_redacted_confirmed
            ):
                raise ValueError("EXTERNAL_CALL_DENIED")
            if (
                self.projects is not None
                and not self.projects.get(command.project_id).allow_external_model
            ):
                raise ValueError("EXTERNAL_CALL_DENIED")
        if command.uploaded_bytes is not None:
            payload = command.uploaded_bytes
            filename = command.uploaded_name
        else:
            if command.local_path is None:
                raise ValueError("MATERIAL_UPLOAD_REQUIRED")
            local_path = command.local_path.expanduser().resolve()
            payload = local_path.read_bytes()
            filename = local_path.name
        if filename is None:
            raise ValueError("MATERIAL_UPLOAD_REQUIRED")
        digest = hashlib.sha256(payload).hexdigest()
        existing = self.sources.find_by_sha256(command.project_id, digest)
        if existing is not None:
            return self._view(existing, duplicate=True)
        series_id = f"MAT-{command.project_id}-{digest[:12].upper()}"
        previous_source_id: str | None = None
        material_name = command.material_name
        if command.archive_mode.value == "new_version":
            if not command.target_series_id:
                raise ValueError("MATERIAL_SERIES_REQUIRED")
            latest = self.sources.find_latest_for_series(
                command.project_id, command.target_series_id
            )
            if latest is None:
                raise ValueError("MATERIAL_SERIES_NOT_FOUND")
            if (
                self.sources.find_by_series_version(
                    command.project_id, command.target_series_id, command.material_version or ""
                )
                is not None
            ):
                raise ValueError("MATERIAL_VERSION_DUPLICATE")
            series_id = latest.material_series_id or command.target_series_id
            previous_source_id = latest.id
            material_name = latest.material_name or latest.original_filename.rsplit(".", 1)[0]
        source_id = f"SRC-{command.project_id}-{digest[:16].upper()}"
        archive = self.archive_factory(source_id, command.document_date.year)
        archived = archive.save(filename, payload)
        mime_type = detect_mime_type(payload)
        if mime_type is None:
            raise ValueError("archive MIME type is invalid")
        source = SourceRecord(
            id=source_id,
            project_id=command.project_id,
            original_filename=filename,
            archive_path=str(archived.path),
            sha256=archived.sha256,
            mime_type=mime_type,
            size_bytes=archived.size_bytes,
            source_type=command.source_type,
            authority_level=command.authority_level,
            source_department=command.source_department,
            provider=None,
            document_date=command.document_date,
            document_version=command.material_version or command.document_version or "",
            applicable_baseline_version="未关联基线",
            security_level=command.security_level,
            is_redacted=command.is_redacted_confirmed,
            allow_external_model=command.allow_external_model,
            is_sandbox=False,
            ingest_status="archived",
            created_at=self.now(),
            material_name=material_name,
            material_series_id=series_id,
            previous_source_id=previous_source_id,
        )
        try:
            self.sources.add(source)
            self.index.upsert(source)
        except (OSError, ValueError):
            self.sources.delete(source.id, source.project_id)
            archive.discard_uncommitted(archived)
            raise RuntimeError("SOURCE_ARCHIVE_COMMIT_FAILED") from None
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
            material_name=source.material_name,
            material_series_id=source.material_series_id,
            previous_source_id=source.previous_source_id,
        )
