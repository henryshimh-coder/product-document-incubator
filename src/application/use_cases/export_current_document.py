from __future__ import annotations

import hashlib
import re

from src.application.dto.documents import ExportCurrentDocumentInput, ExportedDocument
from src.application.ports.repositories import ProjectRepository
from src.domain.errors import DomainError, ErrorCode
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import ProjectPaths

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


class ExportCurrentDocument:
    """Return the one Owner-effective Markdown document as an attachment-ready file."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        projects: ProjectRepository,
        manifest: ManifestStore,
        store: DocumentStore | None = None,
    ) -> None:
        self.paths = paths
        self.projects = projects
        self.manifest = manifest
        self.store = store or DocumentStore(paths)

    def execute(self, command: ExportCurrentDocumentInput) -> ExportedDocument:
        if command.project_id != self.paths.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        project = self.projects.get(command.project_id)
        if not self.paths.manifest_path.is_file():
            raise DomainError(ErrorCode.BASELINE_NOT_FOUND)
        try:
            manifest = self.manifest.read_and_validate()
        except ValueError as error:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "MANIFEST_INVALID") from error
        if manifest.project_id != project.id:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "MANIFEST_PROJECT_MISMATCH")
        current_path = self.paths.wiki_root / "current" / "当前产品方案.md"
        version_path = (self.paths.project_root / manifest.full_document_path).resolve()
        versions_root = (self.paths.wiki_root / "versions").resolve()
        if (
            not current_path.is_file()
            or not version_path.is_file()
            or not version_path.is_relative_to(versions_root)
        ):
            raise DomainError(ErrorCode.BASELINE_NOT_FOUND)
        content = current_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != manifest.full_document_sha256 or version_path.read_bytes() != content:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "CURRENT_DOCUMENT_HASH_MISMATCH")
        display_version = manifest.display_version or manifest.current_version
        filename = _INVALID_FILENAME_CHARS.sub("_", f"{project.name}_产品方案_{display_version}.md")
        export_path = self.store.write_export(filename, content)
        return ExportedDocument(
            filename=filename,
            content=content,
            sha256=digest,
            export_path=export_path,
        )
